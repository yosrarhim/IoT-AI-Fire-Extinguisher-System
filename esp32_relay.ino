// esp32_relay.ino
// ESP32 MQTT subscriber to control a pump and an LED (via relays)
// Author: Yosra Rhim
// Wiring:
//  - RELAY_PUMP to GPIO 13
//  - RELAY_LED  to GPIO 12
//  - Make sure to use proper power for relay module and pump (external supply for pump)

#include <WiFi.h>
#include <PubSubClient.h>

///////////////////// CONFIG /////////////////////
const char* ssid = "YOUR_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

const char* mqtt_server = "192.168.1.100"; // put your broker IP here
const uint16_t mqtt_port = 1883;
const char* mqtt_user = ""; // if used
const char* mqtt_pass = "";

const char* topic_sub = "home/fire";

const int RELAY_PUMP = 13; // GPIO pin controlling relay for pump
const int RELAY_LED  = 12; // GPIO pin controlling relay for LED / buzzer

// Safety parameters
const unsigned long AUTO_OFF_MS = 60000UL; // auto-off after 60s if no ON message refresh

///////////////////// GLOBALS /////////////////////
WiFiClient espClient;
PubSubClient client(espClient);
unsigned long last_on_time = 0;
bool is_on = false;

///////////////////// HELPERS /////////////////////
void setRelays(bool on) {
  // Assuming relay module is active LOW (common). Adjust logic if module is active HIGH.
  // If your relay is active HIGH, invert the digitalWrite values.
  digitalWrite(RELAY_PUMP, on ? LOW : HIGH);
  digitalWrite(RELAY_LED, on ? LOW : HIGH);
  is_on = on;
  if(on) last_on_time = millis();
}

void callback(char* topic, byte* payload, unsigned int length) {
  String msg;
  for(unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();
  Serial.print("[MQTT] Msg on ");
  Serial.print(topic);
  Serial.print(" => ");
  Serial.println(msg);

  if(String(topic) == topic_sub) {
    if(msg.equalsIgnoreCase("ON")) {
      setRelays(true);
    } else if(msg.equalsIgnoreCase("OFF")) {
      setRelays(false);
    }
  }
}

void reconnect() {
  while(!client.connected()) {
    Serial.print("Attempting MQTT connection...");
    String clientId = "ESP32-Relay-";
    clientId += String((uint32_t)esp_random(), HEX);
    if(client.connect(clientId.c_str(), mqtt_user, mqtt_pass)) {
      Serial.println("connected");
      client.subscribe(topic_sub);
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 2s");
      delay(2000);
    }
  }
}

///////////////////// ARDUINO SETUP /////////////////////
void setup() {
  Serial.begin(115200);

  pinMode(RELAY_PUMP, OUTPUT);
  pinMode(RELAY_LED, OUTPUT);
  // default all off: set HIGH if relay active LOW
  digitalWrite(RELAY_PUMP, HIGH);
  digitalWrite(RELAY_LED, HIGH);

  WiFi.begin(ssid, password);
  Serial.print("Connecting WiFi");
  while(WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("WiFi connected, IP: ");
  Serial.println(WiFi.localIP());

  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

void loop() {
  if(!client.connected()) {
    reconnect();
  }
  client.loop();

  // Auto-off safety
  if(is_on && (millis() - last_on_time > AUTO_OFF_MS)) {
    Serial.println("Auto-off: no recent ON message");
    setRelays(false);
  }
}
