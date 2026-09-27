#include <Arduino.h>
#include <SPI.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <Adafruit_ST7789.h>
#include <algorithm>
#include <atomic>
#include <vector>
#include "pelican_frames.h"

constexpr int PIN_DC = 4;
constexpr int PIN_CS = 5;
constexpr int PIN_SCK = 6;
constexpr int PIN_MOSI = 7;
constexpr int PIN_RST = 8;
constexpr int PIN_KEY = 9;           // Onboard KEY, active low (also the BOOT strap).
constexpr uint32_t DEBOUNCE_MS = 30;
constexpr uint32_t HOLD_MODE_MS = 3000;
constexpr uint32_t HOLD_SETUP_MS = 10000;
constexpr uint32_t BANNER_MS = 2500;
constexpr uint32_t SETUP_REDRAW_MS = 250;
constexpr uint16_t PAPER = 0xff37;   // RGB(250, 228, 185), background of the animation
constexpr uint16_t ALERT = 0xe163;   // RGB(225, 45, 25)
constexpr uint16_t INK = 0x28e4;     // RGB(40, 30, 36)
constexpr uint32_t SPI_FREQ = 40000000;

const char* const API_URL = "http://43.110.35.68:8787/codex-reset/v2";
constexpr uint32_t POLL_MS = 5 * 60 * 1000;
constexpr uint32_t WIFI_TIMEOUT_MS = 15000;
constexpr uint32_t HTTP_TIMEOUT_MS = 8000;
constexpr uint32_t PORTAL_CONNECT_MS = 20000;
constexpr uint32_t PORTAL_DONE_MS = 8000;       // keep the hotspot up so the phone sees "ok"
constexpr uint32_t PORTAL_IDLE_MS = 10 * 60 * 1000;

// Demo: KEY cycles the scenes. Live: the reset API picks the scene.
// Setup: a hotspot and captive page for entering the Wi-Fi from a phone.
enum AppMode : uint8_t { MODE_DEMO, MODE_LIVE, MODE_SETUP };
enum NetStatus : uint8_t { NET_PENDING, NET_OK, NET_NO_WIFI, NET_HTTP_ERROR, NET_BAD_JSON };
enum PortalState : uint8_t { PORTAL_WAIT, PORTAL_CONNECTING, PORTAL_FAIL, PORTAL_OK };

Adafruit_ST7789 tft(&SPI, PIN_CS, PIN_DC, PIN_RST);
// Each frame is decoded off-screen and pushed at once, so the panel never
// shows a half-drawn frame.
GFXcanvas16 canvas(240, 240);
Preferences prefs;
TaskHandle_t netTask = nullptr;
WebServer server(80);
DNSServer dns;
char apName[16];

std::atomic<uint8_t> appMode{MODE_DEMO};
std::atomic<uint8_t> modeBeforeSetup{MODE_DEMO};
std::atomic<bool> haveCreds{false};
std::atomic<uint8_t> liveScene{SCENE_IDLE};
std::atomic<uint8_t> netStatus{NET_PENDING};
std::atomic<uint8_t> portalState{PORTAL_WAIT};
std::atomic<bool> phoneConnected{false};

uint8_t demoScene = SCENE_IDLE;
uint8_t sceneIndex = SCENE_IDLE;
uint8_t frameIndex = 0;
uint32_t nextFrameAt = 0;
uint32_t bannerUntil = 0;
const PelicanImage* banner = nullptr;

bool keyRaw = HIGH;
bool keyStable = HIGH;
uint32_t keyChangedAt = 0;
uint32_t keyDownAt = 0;

uint32_t reportStart = 0;
uint32_t framesSinceReport = 0;
uint32_t drawTimeSum = 0;
uint32_t drawTimeMax = 0;

// ---------------------------------------------------------------- drawing

// Frames are (run, colorHi, colorLo) triplets from render_pelican.py, row-major.
// Written back to front: setRotation(0) is the panel offset that works, but the
// board mounts the panel upside down, so the image is turned 180° here.
void decodeFrame(const PelicanScene& scene, uint8_t index) {
    const uint8_t* p = scene.frames[index];
    const uint8_t* end = p + scene.sizes[index];
    uint16_t* out = canvas.getBuffer() + 240*240;
    while (p < end) {
        uint8_t run = p[0];
        const uint16_t color = (uint16_t)p[1] << 8 | p[2];
        while (run--) *--out = color;
        p += 3;
    }
}

// Same encoding at (x0, y0); keyed images leave PAPER pixels untouched.
void drawImage(const PelicanImage& img, int x0, int y0) {
    uint16_t* buf = canvas.getBuffer();
    uint32_t i = 0;
    for (const uint8_t* p = img.data, *end = img.data + img.size; p < end; p += 3) {
        const uint16_t color = (uint16_t)p[1] << 8 | p[2];
        for (uint8_t run = p[0]; run--; ++i) {
            if (img.keyed && color == PAPER) continue;
            const int x = x0 + i % img.w, y = y0 + i / img.w;
            buf[240*240 - 1 - (y*240 + x)] = color;
        }
    }
}

void drawCentred(const PelicanImage& img) {
    drawImage(img, (240 - img.w) / 2, (240 - img.h) / 2);
}

void presentFrame() {
    tft.startWrite();
    tft.setAddrWindow(0, 0, 240, 240);
    tft.writePixels(canvas.getBuffer(), 240*240);
    tft.endWrite();
}

// Panel power / VCOM / gamma settings copied from the init table in the stock
// pyClock firmware (backups/original-esp32c3-2026-09-27.bin @ 0x430c0).
// Adafruit's ST7789 init leaves these at chip defaults, which washes out
// mid-tones into a gray, blue-purple cast.
void applyPanelTuning() {
    static const uint8_t porch[] = {0x0c, 0x0c, 0x00, 0x33, 0x33};
    static const uint8_t gate[] = {0x35};
    static const uint8_t vcom[] = {0x32};
    static const uint8_t vdvvrhEnable[] = {0x01};
    static const uint8_t vrh[] = {0x15};
    static const uint8_t vdv[] = {0x20};
    static const uint8_t frameRate[] = {0x0f};
    static const uint8_t power[] = {0xa4, 0xa1};
    static const uint8_t gammaPos[] = {0xd0,0x08,0x0e,0x09,0x09,0x05,0x31,0x33,0x48,0x17,0x14,0x15,0x31,0x34};
    static const uint8_t gammaNeg[] = {0xd0,0x08,0x0e,0x09,0x09,0x15,0x31,0x33,0x48,0x17,0x14,0x15,0x31,0x34};
    tft.sendCommand(0xb2, porch, sizeof porch);
    tft.sendCommand(0xb7, gate, sizeof gate);
    tft.sendCommand(0xbb, vcom, sizeof vcom);
    tft.sendCommand(0xc2, vdvvrhEnable, sizeof vdvvrhEnable);
    tft.sendCommand(0xc3, vrh, sizeof vrh);
    tft.sendCommand(0xc4, vdv, sizeof vdv);
    tft.sendCommand(0xc6, frameRate, sizeof frameRate);
    tft.sendCommand(0xd0, power, sizeof power);
    tft.sendCommand(0xe0, gammaPos, sizeof gammaPos);
    tft.sendCommand(0xe1, gammaNeg, sizeof gammaNeg);
}

// ---------------------------------------------------------------- live mode

bool connectWifi() {
    if (WiFi.status() == WL_CONNECTED) return true;
    const String ssid = prefs.getString("ssid", "");
    const String pass = prefs.getString("pass", "");
    if (ssid.isEmpty()) return false;
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), pass.c_str());
    const uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS) {
        if (appMode != MODE_LIVE) return false;
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    Serial.printf("wifi: %s\n", WiFi.status() == WL_CONNECTED ? "connected" : "timeout");
    return WiFi.status() == WL_CONNECTED;
}

// Upcoming reset beats a finished one: the warning is the newer news.
uint8_t sceneFor(bool upcoming, bool completed) {
    if (upcoming) return SCENE_RIDE;
    if (completed) return SCENE_REST;
    return SCENE_IDLE;
}

void pollApi() {
    if (!connectWifi()) {
        netStatus = NET_NO_WIFI;
        return;
    }
    HTTPClient http;
    http.setTimeout(HTTP_TIMEOUT_MS);
    http.begin(API_URL);
    const int code = http.GET();
    if (code != HTTP_CODE_OK) {
        Serial.printf("api: http %d\n", code);
        http.end();
        netStatus = NET_HTTP_ERROR;
        return;
    }
    const String body = http.getString();
    http.end();
    JsonDocument doc;
    if (deserializeJson(doc, body)) {
        Serial.println("api: bad json");
        netStatus = NET_BAD_JSON;
        return;
    }
    const bool upcoming = doc["upcoming_within_36h"] | false;
    const bool completed = doc["public_reset_completed_within_72h"] | false;
    liveScene = sceneFor(upcoming, completed);
    netStatus = NET_OK;
    Serial.printf("api: upcoming=%d completed=%d -> scene=%u\n", upcoming, completed,
                  (unsigned)liveScene);
}

// ---------------------------------------------------------------- setup portal

static const char PAGE_HEAD[] PROGMEM = R"html(<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>给鹈鹕配网</title><style>
body{margin:0;padding:24px 20px;font-family:-apple-system,system-ui,sans-serif;background:#fae4b9;color:#281e24}
h1{font-size:24px;margin:0 0 4px}.sub{margin:0 0 8px;color:#6b5a52}
label{display:block;font-weight:600;margin:18px 0 6px}
select,input,button{width:100%;box-sizing:border-box;font-size:17px;padding:12px 14px;border:2px solid #281e24;border-radius:12px;background:#fffcf0;color:#281e24}
input[name=manual]{margin-top:8px}
button{margin-top:24px;background:#281e24;color:#fffcf0;font-weight:700}
button:disabled{opacity:.5}a{color:#008a88}
#msg{margin-top:18px;font-weight:600;min-height:1.4em}.ok{color:#008a88}.bad{color:#e12d19}
</style></head><body>
<h1>给鹈鹕连上 Wi-Fi</h1><p class="sub">只支持 2.4 GHz 网络。</p>
<form id="f"><label for="s">Wi-Fi 名称</label><select id="s" name="ssid">)html";

static const char PAGE_TAIL[] PROGMEM = R"html(</select>
<input name="manual" placeholder="列表里没有？在这里手动输入" autocomplete="off" autocapitalize="off">
<p style="margin:8px 0 0"><a href="/?scan=1">重新扫描</a></p>
<label for="p">密码</label><input id="p" name="pass" type="password">
<button id="b">连接</button></form><p id="msg"></p>
<script>
const f=document.getElementById('f'),b=document.getElementById('b'),m=document.getElementById('msg');
function show(t,c){m.textContent=t;m.className=c||''}
f.onsubmit=async e=>{e.preventDefault();b.disabled=true;show('正在连接，大约要 20 秒…');
 try{const r=await fetch('/save',{method:'POST',body:new URLSearchParams(new FormData(f))});
  if(!r.ok){show(await r.text(),'bad');b.disabled=false;return}}catch(_){}
 poll()};
async function poll(){let s='connecting';
 try{s=(await (await fetch('/status',{cache:'no-store'})).json()).state}catch(_){}
 if(s==='ok'){show('连好了！鹈鹕马上进入正式模式，可以关掉这个页面。','ok');return}
 if(s==='fail'){show('连接失败，请检查密码后重试。','bad');b.disabled=false;return}
 setTimeout(poll,1000)}
</script></body></html>)html";

String ssidOptions;
String pendingSsid, pendingPass;
bool pendingConnect = false;
uint32_t portalActivityAt = 0;

String htmlEscape(const String& s) {
    String out;
    for (char c : s) {
        if (c == '&') out += "&amp;";
        else if (c == '<') out += "&lt;";
        else if (c == '>') out += "&gt;";
        else if (c == '"') out += "&quot;";
        else out += c;
    }
    return out;
}

// Unique names, strongest signal first.
void scanNetworks() {
    const int n = WiFi.scanNetworks();
    std::vector<int> order;
    for (int i = 0; i < n; ++i) {
        if (WiFi.SSID(i).isEmpty()) continue;
        bool dup = false;
        for (int j : order) dup |= WiFi.SSID(j) == WiFi.SSID(i);
        if (!dup) order.push_back(i);
    }
    std::sort(order.begin(), order.end(), [](int a, int b) { return WiFi.RSSI(a) > WiFi.RSSI(b); });
    ssidOptions = "";
    for (int i : order) {
        const String name = htmlEscape(WiFi.SSID(i));
        ssidOptions += "<option value=\"" + name + "\">" + name + "</option>";
    }
    if (ssidOptions.isEmpty()) ssidOptions = "<option value=\"\">（没扫描到网络）</option>";
    WiFi.scanDelete();
    Serial.printf("setup: %d networks\n", (int)order.size());
}

void handleRoot() {
    portalActivityAt = millis();
    if (server.hasArg("scan")) scanNetworks();
    String page = FPSTR(PAGE_HEAD);
    page += ssidOptions;
    page += FPSTR(PAGE_TAIL);
    server.sendHeader("Cache-Control", "no-store");
    server.send(200, "text/html; charset=utf-8", page);
}

void handleSave() {
    portalActivityAt = millis();
    String ssid = server.arg("manual");
    ssid.trim();
    if (ssid.isEmpty()) ssid = server.arg("ssid");
    if (ssid.isEmpty()) {
        server.send(400, "text/plain; charset=utf-8", "请先选择或输入 Wi-Fi 名称。");
        return;
    }
    pendingSsid = ssid;
    pendingPass = server.arg("pass");
    pendingConnect = true;
    portalState = PORTAL_CONNECTING;
    server.send(200, "application/json", "{\"ok\":true}");
}

void handleStatus() {
    portalActivityAt = millis();
    static const char* const names[] = {"wait", "connecting", "fail", "ok"};
    server.sendHeader("Cache-Control", "no-store");
    server.send(200, "application/json", String("{\"state\":\"") + names[portalState] + "\"}");
}

// Every unknown URL, including the phones' captive-portal probes, lands on the form.
void handleRedirect() {
    server.sendHeader("Location", "http://192.168.4.1/");
    server.send(302, "text/plain", "");
}

uint8_t modeAfterSetup() {
    const uint8_t m = modeBeforeSetup;
    return (m == MODE_LIVE && haveCreds) ? MODE_LIVE : MODE_DEMO;
}

void runPortal() {
    WiFi.disconnect(true);
    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(apName);
    vTaskDelay(pdMS_TO_TICKS(200));
    dns.setErrorReplyCode(DNSReplyCode::NoError);
    dns.start(53, "*", WiFi.softAPIP());
    scanNetworks();
    server.begin();
    portalState = PORTAL_WAIT;
    portalActivityAt = millis();
    uint32_t connectStart = 0, doneAt = 0;
    Serial.printf("setup: hotspot %s up, heap=%u\n", apName, ESP.getFreeHeap());

    while (appMode == MODE_SETUP) {
        dns.processNextRequest();
        server.handleClient();
        const bool phone = WiFi.softAPgetStationNum() > 0;
        if (phone && !phoneConnected) portalActivityAt = millis();
        phoneConnected = phone;

        if (pendingConnect) {
            pendingConnect = false;
            WiFi.disconnect(false);
            WiFi.begin(pendingSsid.c_str(), pendingPass.c_str());
            connectStart = millis();
            Serial.printf("setup: trying %s\n", pendingSsid.c_str());
        }
        if (portalState == PORTAL_CONNECTING && connectStart) {
            if (WiFi.status() == WL_CONNECTED) {
                prefs.putString("ssid", pendingSsid);
                prefs.putString("pass", pendingPass);
                prefs.putBool("live", true);
                haveCreds = true;
                portalState = PORTAL_OK;
                doneAt = millis();
                Serial.println("setup: connected, saved");
            } else if (millis() - connectStart > PORTAL_CONNECT_MS) {
                WiFi.disconnect(false);
                portalState = PORTAL_FAIL;
                connectStart = 0;
                Serial.println("setup: connect failed");
            }
        }
        if (portalState == PORTAL_OK && millis() - doneAt > PORTAL_DONE_MS) {
            appMode = MODE_LIVE;
        } else if (portalState != PORTAL_CONNECTING && portalState != PORTAL_OK &&
                   millis() - portalActivityAt > PORTAL_IDLE_MS) {
            Serial.println("setup: idle timeout");
            appMode = modeAfterSetup();
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }

    server.stop();
    dns.stop();
    WiFi.softAPdisconnect(true);
    WiFi.mode(appMode == MODE_LIVE && WiFi.status() == WL_CONNECTED ? WIFI_STA : WIFI_OFF);
    phoneConnected = false;
}

// ---------------------------------------------------------------- network task

// Runs beside the animation so a slow network never stalls a frame.
// Woken early by a mode change or a short press in live mode.
void netLoop(void*) {
    for (;;) {
        switch (appMode.load()) {
        case MODE_SETUP:
            runPortal();
            break;
        case MODE_LIVE:
            pollApi();
            ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(POLL_MS));
            break;
        default:
            if (WiFi.getMode() != WIFI_OFF) {
                WiFi.disconnect(true);
                WiFi.mode(WIFI_OFF);
            }
            netStatus = NET_PENDING;
            ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        }
    }
}

// ---------------------------------------------------------------- key

void showBanner(const PelicanImage& img) {
    banner = &img;
    bannerUntil = millis() + BANNER_MS;
}

void setMode(uint8_t mode) {
    appMode = mode;
    frameIndex = 0;
    nextFrameAt = millis();
    Serial.printf("mode=%s\n", mode == MODE_LIVE ? "live" : mode == MODE_SETUP ? "setup" : "demo");
    if (netTask) xTaskNotifyGive(netTask);
}

void enterSetup(uint8_t returnTo) {
    modeBeforeSetup = returnTo;
    setMode(MODE_SETUP);
}

void toggleMode() {
    if (appMode == MODE_LIVE) {
        prefs.putBool("live", false);
        setMode(MODE_DEMO);
        showBanner(BANNER_DEMO);
    } else if (!haveCreds) {
        enterSetup(MODE_DEMO);  // Nothing to connect to yet: go straight to setup.
    } else {
        prefs.putBool("live", true);
        setMode(MODE_LIVE);
        showBanner(BANNER_LIVE);
    }
}

void onRelease(uint32_t held) {
    if (appMode == MODE_SETUP) {
        setMode(modeAfterSetup());
        return;
    }
    if (held >= HOLD_SETUP_MS) {
        enterSetup(appMode);
    } else if (held >= HOLD_MODE_MS) {
        toggleMode();
    } else if (appMode == MODE_LIVE) {
        Serial.println("key: refresh");
        xTaskNotifyGive(netTask);
    } else {
        demoScene = (demoScene + 1) % PELICAN_SCENE_COUNT;
        Serial.printf("key: demo scene=%u\n", demoScene);
    }
}

// Every action happens on release; while held, the screen says what release will do.
void pollKey() {
    const bool raw = digitalRead(PIN_KEY);
    if (raw != keyRaw) {
        keyRaw = raw;
        keyChangedAt = millis();
    }
    if (raw == keyStable || millis() - keyChangedAt < DEBOUNCE_MS) return;
    keyStable = raw;
    if (keyStable == LOW) keyDownAt = millis();
    else onRelease(millis() - keyDownAt);
}

const PelicanImage* holdHint() {
    if (keyStable != LOW || appMode == MODE_SETUP) return nullptr;
    const uint32_t held = millis() - keyDownAt;
    if (held >= HOLD_SETUP_MS) return &BANNER_TO_SETUP;
    if (held >= HOLD_MODE_MS) return appMode == MODE_LIVE ? &BANNER_TO_DEMO : &BANNER_TO_LIVE;
    return nullptr;
}

// ---------------------------------------------------------------- main

void drawSetupScreen() {
    drawImage(SETUP_SCREEN, 0, 0);
    canvas.setTextSize(2);
    canvas.setTextColor(INK);
    canvas.setCursor((240 - (int)strlen(apName) * 12) / 2, (SSID_BOX_Y0 + SSID_BOX_Y1) / 2 - 7);
    canvas.print(apName);
    const PelicanImage* status = &STATUS_WAIT;
    switch (portalState.load()) {
    case PORTAL_CONNECTING: status = &STATUS_CONNECTING; break;
    case PORTAL_FAIL: status = &STATUS_FAIL; break;
    case PORTAL_OK: status = &STATUS_OK; break;
    default: if (phoneConnected) status = &STATUS_PHONE;
    }
    drawImage(*status, (240 - status->w) / 2, STATUS_Y);
    presentFrame();
}

#ifdef COLOR_TEST
// Six numbered bands in known RGB565 values to diagnose byte order / inversion / BGR.
void drawColorTest() {
    const uint16_t bands[] = {0xf800, 0x07e0, 0x001f, 0xffff, 0x0000, PAPER};
    for (int i = 0; i < 6; ++i) {
        canvas.fillRect(0, i*40, 240, 40, bands[i]);
        canvas.fillRect(8, i*40+6, 28, 28, 0x0000);
        canvas.drawRect(8, i*40+6, 28, 28, 0xffff);
        canvas.setTextSize(3);
        canvas.setTextColor(0xffff);
        canvas.setCursor(14, i*40+9);
        canvas.print(i+1);
    }
    presentFrame();
}
#endif

void setup() {
    Serial.begin(115200);
    delay(200);
    pinMode(PIN_KEY, INPUT_PULLUP);
    SPI.begin(PIN_SCK, -1, PIN_MOSI, PIN_CS);
    tft.init(240,240,SPI_MODE0);
    applyPanelTuning();
    tft.setSPISpeed(SPI_FREQ);
    tft.setRotation(0);
    if (!canvas.getBuffer()) {
        Serial.println("frame buffer allocation failed");
        while (true) delay(1000);
    }
    canvas.setRotation(2); // GFX drawing matches the 180° turn in decodeFrame.

    const uint64_t mac = ESP.getEfuseMac();
    snprintf(apName, sizeof apName, "Pelican-%02X%02X",
             (unsigned)(mac >> 32) & 0xff, (unsigned)(mac >> 40) & 0xff);
    server.on("/", handleRoot);
    server.on("/save", HTTP_POST, handleSave);
    server.on("/status", handleStatus);
    server.onNotFound(handleRedirect);

    prefs.begin("pelican");
    haveCreds = !prefs.getString("ssid", "").isEmpty();
    if (prefs.getBool("live", false)) {
        if (haveCreds) appMode = MODE_LIVE;
        else { modeBeforeSetup = MODE_DEMO; appMode = MODE_SETUP; }
    }
    xTaskCreate(netLoop, "net", 8192, nullptr, 1, &netTask);
    reportStart=millis();
    nextFrameAt=reportStart;
    Serial.printf("pyClock pelican started, mode=%u, wifi saved=%d\n",
                  (unsigned)appMode.load(), (int)haveCreds.load());
}

void loop() {
#ifdef COLOR_TEST
    static bool shown = false;
    if (!shown) { drawColorTest(); shown = true; Serial.println("color test shown"); }
    delay(100);
    return;
#endif
    pollKey();
    if (appMode == MODE_SETUP) {
        if ((int32_t)(millis()-nextFrameAt)>=0) {
            drawSetupScreen();
            nextFrameAt = millis() + SETUP_REDRAW_MS;
        }
        delay(1);
        return;
    }

    const uint8_t wanted = appMode == MODE_LIVE ? liveScene.load() : demoScene;
    if (wanted != sceneIndex) {
        sceneIndex = wanted;
        frameIndex = 0;
        nextFrameAt = millis();
    }
    const PelicanScene& scene = PELICAN_SCENES[sceneIndex];
    if ((int32_t)(millis()-nextFrameAt)>=0) {
        const uint32_t start=millis();
        decodeFrame(scene, frameIndex);
        if (const PelicanImage* hint = holdHint()) drawCentred(*hint);
        else if (banner && (int32_t)(millis()-bannerUntil)<0) drawCentred(*banner);
        const uint8_t net = netStatus;
        if (appMode == MODE_LIVE && net != NET_OK && net != NET_PENDING) {
            canvas.fillCircle(229, 11, 5, INK);  // Offline: small red dot, top right.
            canvas.fillCircle(229, 11, 3, ALERT);
        }
        presentFrame();
        const uint32_t duration=millis()-start;
        drawTimeSum+=duration;
        if (duration>drawTimeMax) drawTimeMax=duration;
        frameIndex=(frameIndex+1)%scene.count;
        nextFrameAt+=scene.frameMs;
        if ((int32_t)(millis()-nextFrameAt)>0) nextFrameAt=millis();
        ++framesSinceReport;
    }
    if (millis()-reportStart>=5000) {
        Serial.printf("frames=%lu interval_ms=%lu fps=%.1f avg_draw_ms=%.1f max_draw_ms=%lu heap=%u\n",
            (unsigned long)framesSinceReport,(unsigned long)(millis()-reportStart),
            framesSinceReport*1000.0f/(millis()-reportStart),
            framesSinceReport?(float)drawTimeSum/framesSinceReport:0.0f,
            (unsigned long)drawTimeMax, ESP.getFreeHeap());
        framesSinceReport=0;drawTimeSum=0;drawTimeMax=0;reportStart=millis();
    }
    delay(1);
}
