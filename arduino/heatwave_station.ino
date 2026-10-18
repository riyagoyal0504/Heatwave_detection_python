/*
 * ============================================================================
 *  Heatwave Alert Station  -  Arduino Uno (built and simulated in Tinkercad)
 * ============================================================================
 *  The local, physical end of the project. It does two jobs:
 *
 *   1. SENSE: reads temperature + humidity every 2 s, judges the local reading
 *      with the IMD thresholds, and prints one CSV line per reading:
 *          millis,temperature,humidity        e.g.  12000,41.5,32.0
 *
 *   2. WARN: receives the 3-day heat-wave prediction from the Python backend
 *      as one serial line:
 *          F,<lvl today>,<lvl tomorrow>,<lvl day after>,<t0>,<t1>,<t2>
 *          e.g.  F,1,2,2,44,46,47      lvl: 0 normal, 1 heat wave, 2 severe
 *      and shows it on the LCD (the screen alternates between the local
 *      reading and the forecast).
 *
 *  Outputs
 *    LEDs   : green / yellow / red = the WORST of (local level, forecast level)
 *    Buzzer : beeps ONLY when that worst level is SEVERE; the button mutes it
 *    Button : hardware interrupt on D2 silences the buzzer until the level
 *             drops below severe again
 *
 *  Sensor choice (USE_DHT22 below)
 *    1 = DHT22 on D7 (real hardware, or if your Tinkercad offers DHT22 + library)
 *    0 = TMP36 on A0 + potentiometer on A1 for humidity (works with the parts
 *        every Tinkercad account has)
 *
 *  NOTE: this sketch has not been compiled or run by the author of the
 *  project files; build it in Tinkercad first and fix any message it shows.
 * ============================================================================
 */

#include <LiquidCrystal.h>

// ------------------------- user settings ------------------------------------
#define USE_DHT22        1      // 1 = DHT22 sensor, 0 = TMP36 + potentiometer

// 1 = start with a sample forecast already loaded (F,1,2,2,44,46,47), so you can
// demonstrate the forecast screen / LEDs / buzzer even if your Tinkercad
// Serial Monitor has no box for sending text. Set to 0 for normal use.
#define PRELOAD_FORECAST 0

// Local alert rule (simplified IMD): 40 = plains, 37 = coastal, 30 = hilly
const float STATION_THRESHOLD_C = 40.0;
// Normal (climatological) maximum temperature for your place and month
const float NORMAL_MAX_C        = 39.0;

const unsigned long SAMPLE_INTERVAL_MS = 2000;  // DHT22 cannot be read faster
const unsigned long PAGE_INTERVAL_MS   = 3000;  // LCD page flip time
const unsigned long RX_TIMEOUT_MS      = 150;   // accept a line with no newline
// ----------------------------------------------------------------------------

#if USE_DHT22
  #include <DHT.h>                // "DHT sensor library" by Adafruit
  #define DHT_PIN   7
  DHT dht(DHT_PIN, DHT22);
#else
  #define TMP36_PIN A0            // TMP36 output
  #define POT_PIN   A1            // potentiometer wiper = fake humidity
#endif

// ------------------------- pin assignments ----------------------------------
const int PIN_MUTE_BTN   = 2;     // D2 = INT0, hardware interrupt pin
const int PIN_LED_GREEN  = 3;
const int PIN_LED_YELLOW = 4;
const int PIN_LED_RED    = 5;
const int PIN_BUZZER     = 6;

//                RS  E  D4  D5 D6 D7
LiquidCrystal lcd(12, 11, 10, 9, 8, 13);

// ------------------------- alert levels -------------------------------------
const int LEVEL_NORMAL = 0;
const int LEVEL_HEAT   = 1;
const int LEVEL_SEVERE = 2;

float heatWaveTemp;               // computed once in setup()
float severeTemp;

// Local sensor state
float lastTemp = 0.0;
float lastHum  = 0.0;
bool  haveReading = false;
bool  sensorError = false;
int   localLevel  = LEVEL_NORMAL;

// Forecast state (filled by the serial command from Python)
int  forecastLevel[3] = {0, 0, 0};
int  forecastTemp[3]  = {0, 0, 0};
bool haveForecast = false;

int  activeLevel = LEVEL_NORMAL;  // worst of local and forecast: drives LEDs/buzzer

// Timing
unsigned long lastSampleMs = 0;
unsigned long lastPageMs   = 0;
bool showForecastPage = false;

// Serial receive buffer
char rxBuf[40];
byte rxLen = 0;
unsigned long lastRxMs = 0;

// ------------------------- buzzer / interrupt state -------------------------
// 'volatile' because the ISR changes it behind the back of loop()
volatile bool buzzerMuted = false;
volatile unsigned long lastPressMs = 0;

unsigned long lastBeepToggleMs = 0;
bool beepOn = false;

// Interrupt Service Routine: runs the instant the button is pressed.
// Keep it tiny: set a flag and return. Includes a 200 ms debounce.
void onMutePressed() {
  unsigned long now = millis();
  if (now - lastPressMs > 200) {
    buzzerMuted = true;
    lastPressMs = now;
  }
}

// Forward declarations (the functions are defined further down)
bool parseForecast(const char *s);
void updateOutputs();

// ----------------------------------------------------------------------------
void setup() {
  Serial.begin(9600);             // must match the baud rate used by Python

  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_MUTE_BTN, INPUT_PULLUP);          // pressed = LOW, no resistor needed
  attachInterrupt(digitalPinToInterrupt(PIN_MUTE_BTN), onMutePressed, FALLING);

  lcd.begin(16, 2);
  lcd.print("Heatwave Station");
  lcd.setCursor(0, 1);
  lcd.print("Starting...");

#if USE_DHT22
  dht.begin();
#endif

  // Build the two local thresholds from the settings above
  heatWaveTemp = min(max(STATION_THRESHOLD_C, NORMAL_MAX_C + 4.5), 45.0);
  severeTemp   = min(max(STATION_THRESHOLD_C, NORMAL_MAX_C + 6.5), 47.0);

#if PRELOAD_FORECAST
  parseForecast("F,1,2,2,44,46,47");
  updateOutputs();
#endif

  // CSV header. Python's parser skips this line automatically.
  Serial.println("millis,temperature,humidity");
}

// ----------------------------------------------------------------------------
// Read the sensor. Returns false if the reading failed.
bool readSensor(float &tempC, float &hum) {
#if USE_DHT22
  hum   = dht.readHumidity();
  tempC = dht.readTemperature();              // degrees Celsius
  return !(isnan(hum) || isnan(tempC));
#else
  // TMP36: 0.5 V at 0 C, 10 mV per degree C
  float volts = analogRead(TMP36_PIN) * 5.0 / 1024.0;
  tempC = (volts - 0.5) * 100.0;
  hum   = map(analogRead(POT_PIN), 0, 1023, 10, 95);   // 10..95 %
  return true;
#endif
}

int classify(float t) {
  if (t >= severeTemp)   return LEVEL_SEVERE;
  if (t >= heatWaveTemp) return LEVEL_HEAT;
  return LEVEL_NORMAL;
}

const char* levelName(int level) {
  if (level == LEVEL_SEVERE) return "SEVERE";
  if (level == LEVEL_HEAT)   return "HEATWAVE";
  return "NORMAL";
}

int worstForecast() {
  int w = 0;
  for (int i = 0; i < 3; i++) {
    if (forecastLevel[i] > w) w = forecastLevel[i];
  }
  return w;
}

// LEDs: exactly one lit
void showLeds(int level) {
  digitalWrite(PIN_LED_GREEN,  level == LEVEL_NORMAL);
  digitalWrite(PIN_LED_YELLOW, level == LEVEL_HEAT);
  digitalWrite(PIN_LED_RED,    level == LEVEL_SEVERE);
}

// Recalculate the combined level and update the LEDs
void updateOutputs() {
  activeLevel = localLevel;
  if (haveForecast && worstForecast() > activeLevel) {
    activeLevel = worstForecast();
  }
  if (activeLevel != LEVEL_SEVERE) {
    buzzerMuted = false;          // re-arm the buzzer for the next severe event
  }
  showLeds(activeLevel);
}

// LCD page 1: "T:41.5C H:32%" / "Alert: HEATWAVE"   (local reading)
// LCD page 2: "Fcst 44 46 47 C" / "Worst: SEVERE"    (from Python)
void refreshLcd() {
  lcd.clear();
  if (showForecastPage && haveForecast) {
    lcd.setCursor(0, 0);
    lcd.print("Fcst ");
    lcd.print(forecastTemp[0]); lcd.print(' ');
    lcd.print(forecastTemp[1]); lcd.print(' ');
    lcd.print(forecastTemp[2]); lcd.print(" C");
    lcd.setCursor(0, 1);
    lcd.print("Worst: ");
    lcd.print(levelName(worstForecast()));
  } else if (sensorError) {
    lcd.print("Sensor error");
  } else if (haveReading) {
    lcd.setCursor(0, 0);
    lcd.print("T:");  lcd.print(lastTemp, 1);
    lcd.print("C H:"); lcd.print((int)(lastHum + 0.5)); lcd.print("%");
    lcd.setCursor(0, 1);
    lcd.print("Alert: ");
    lcd.print(levelName(localLevel));
  } else {
    lcd.print("Waiting...");
  }
}

// Parse "F,1,2,2,44,46,47". Returns true if it was a valid forecast line.
bool parseForecast(const char *s) {
  if (s[0] != 'F' || s[1] != ',') return false;
  int vals[6];
  const char *p = s + 2;
  for (int i = 0; i < 6; i++) {
    if (!(isdigit(*p) || *p == '-')) return false;   // reject non-numeric fields
    vals[i] = atoi(p);
    const char *comma = strchr(p, ',');
    if (i < 5) {
      if (comma == NULL) return false;      // not enough fields
      p = comma + 1;
    }
  }
  for (int i = 0; i < 3; i++) {
    forecastLevel[i] = constrain(vals[i], 0, 2);
    forecastTemp[i]  = vals[3 + i];
  }
  haveForecast = true;
  return true;
}

// Collect characters from the serial port; process a line when it ends
// (newline) or when the sender has been quiet for RX_TIMEOUT_MS.
void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    lastRxMs = millis();
    if (c == '\n' || c == '\r') {
      if (rxLen > 0) {
        rxBuf[rxLen] = '\0';
        rxLen = 0;
        if (parseForecast(rxBuf)) {
          Serial.println("# forecast received");   // '#' lines are ignored by Python
          updateOutputs();
          refreshLcd();
        }
      }
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = c;
    }
  }
  if (rxLen > 0 && millis() - lastRxMs > RX_TIMEOUT_MS) {
    rxBuf[rxLen] = '\0';
    rxLen = 0;
    if (parseForecast(rxBuf)) {
      Serial.println("# forecast received");
      updateOutputs();
      refreshLcd();
    }
  }
}

// Non-blocking beeping (250 ms on / 250 ms off) - only on SEVERE and not muted
void updateBuzzer() {
  bool shouldSound = (activeLevel == LEVEL_SEVERE) && !buzzerMuted;
  if (!shouldSound) {
    noTone(PIN_BUZZER);
    beepOn = false;
    return;
  }
  unsigned long now = millis();
  if (now - lastBeepToggleMs >= 250) {
    lastBeepToggleMs = now;
    beepOn = !beepOn;
    if (beepOn) tone(PIN_BUZZER, 2000);
    else        noTone(PIN_BUZZER);
  }
}

// ----------------------------------------------------------------------------
void loop() {
  unsigned long now = millis();

  // 1. Sample the sensor every SAMPLE_INTERVAL_MS (no delay(), so the buzzer
  //    pattern, button and serial input stay responsive).
  if (now - lastSampleMs >= SAMPLE_INTERVAL_MS) {
    lastSampleMs = now;
    float t, h;
    if (readSensor(t, h)) {
      sensorError = false;
      lastTemp = t;
      lastHum  = h;
      haveReading = true;
      localLevel = classify(t);
      updateOutputs();
      refreshLcd();

      // One CSV line per reading: millis,temperature,humidity
      Serial.print(now);  Serial.print(',');
      Serial.print(t, 1); Serial.print(',');
      Serial.println(h, 1);
    } else {
      sensorError = true;       // LCD says so; nothing is printed to the CSV stream
      refreshLcd();
    }
  }

  // 2. Alternate the LCD between the local reading and the forecast
  if (haveForecast && now - lastPageMs >= PAGE_INTERVAL_MS) {
    lastPageMs = now;
    showForecastPage = !showForecastPage;
    refreshLcd();
  }

  // 3. Listen for a forecast line from Python
  readSerialCommands();

  // 4. Buzzer pattern
  updateBuzzer();
}
