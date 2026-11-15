"""Real-time flame detection on an ESP32-CAM video stream.

Pipeline for each frame:
  1. HSV color segmentation with three flame profiles (white-hot, bright core, dancing tip)
  2. Morphological opening + background subtraction (MOG2) to keep only moving bright regions
  3. Contour analysis: area, circularity (flames are irregular) and flicker/motion score
  4. Temporal confirmation: fire must be seen on N consecutive frames before an alert

When fire is confirmed, "ON" is published on the MQTT topic read by the ESP32 actuator
board (esp32_relay.ino), which starts the pump and the alarm. "OFF" is sent once the
fire has not been seen for a while.

Usage:
    python flame_detector.py --stream http://<ESP32-CAM-IP>:81/stream --broker <BROKER-IP>
"""

import argparse
import time
from collections import deque

import cv2
import numpy as np

try:
    import paho.mqtt.client as mqtt
except ImportError:  # MQTT is optional: detection still works without it
    mqtt = None


class FlameDetector:
    def __init__(self, stream_url):
        self.stream_url = stream_url
        self.cap = self._open_stream()

        self.frame_buffer = deque(maxlen=10)

        # Flame characteristics (HSV)
        self.flame_profiles = {
            'white_hot': {
                'lower': np.array([0, 0, 200]),
                'upper': np.array([30, 80, 255]),
                'min_area': 300,
                'max_circularity': 0.3
            },
            'bright_core': {
                'lower': np.array([5, 150, 200]),
                'upper': np.array([25, 255, 255]),
                'min_area': 200,
                'max_circularity': 0.4
            },
            'dancing_tip': {
                'lower': np.array([5, 100, 150]),
                'upper': np.array([25, 255, 255]),
                'min_area': 100,
                'max_circularity': 0.5
            }
        }

        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(history=500)
        self.flame_colors = {
            'white_hot': (0, 0, 255),        # Red for FIRE
            'bright_core': (0, 165, 255),    # Orange
            'dancing_tip': (0, 255, 255)     # Yellow
        }

    def _open_stream(self):
        cap = cv2.VideoCapture(self.stream_url)
        # ESP32-CAM often needs these settings for a stable stream
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FPS, 10)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return cap

    def read_frame(self):
        """Read one frame, reconnecting to the ESP32-CAM if the stream drops."""
        ret, frame = self.cap.read()
        if not ret:
            print("Could not read frame from stream. Reconnecting...")
            self.cap.release()
            time.sleep(1)
            self.cap = self._open_stream()
            return None
        return cv2.resize(frame, (640, 480))

    def get_flame_mask(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        combined_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        for profile in self.flame_profiles.values():
            mask = cv2.inRange(hsv, profile['lower'], profile['upper'])
            combined_mask = cv2.bitwise_or(combined_mask, mask)
        return combined_mask

    def analyze_flame_characteristics(self, contour):
        features = {}
        features['area'] = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        features['circularity'] = 4 * np.pi * features['area'] / (perimeter**2 + 1e-5)
        features['motion_score'] = self.analyze_flame_motion(contour)
        return features

    def analyze_flame_motion(self, contour):
        """Flames flicker: measure pixel change in the region over the last frames."""
        if len(self.frame_buffer) < 2:
            return 0
        x, y, w, h = cv2.boundingRect(contour)
        curr = cv2.cvtColor(self.frame_buffer[-1], cv2.COLOR_BGR2GRAY)
        motion_scores = []
        for i in range(2, min(5, len(self.frame_buffer) + 1)):
            prev = cv2.cvtColor(self.frame_buffer[-i], cv2.COLOR_BGR2GRAY)
            prev_roi = prev[y:y+h, x:x+w]
            curr_roi = curr[y:y+h, x:x+w]
            if prev_roi.size == 0 or curr_roi.size == 0:
                continue
            diff = cv2.absdiff(prev_roi, curr_roi)
            motion_scores.append(np.mean(diff))
        return np.mean(motion_scores) if motion_scores else 0

    def is_real_flame(self, contour, hsv):
        features = self.analyze_flame_characteristics(contour)
        contour_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        cv2.drawContours(contour_mask, [contour], -1, 255, -1)
        for name, profile in self.flame_profiles.items():
            color_mask = cv2.inRange(hsv, profile['lower'], profile['upper'])
            overlap = cv2.bitwise_and(color_mask, contour_mask)
            # Fraction of the contour covered by flame colors (masks are 0/255)
            color_coverage = np.count_nonzero(overlap) / (np.count_nonzero(contour_mask) + 1e-5)
            if (color_coverage > 0.6 and
                features['area'] > profile['min_area'] and
                features['circularity'] < profile['max_circularity'] and
                features['motion_score'] > 5.0):
                return name
        return None

    def process_frame(self, frame):
        """Return (annotated frame, flame mask, fire_detected) for one frame."""
        self.frame_buffer.append(frame.copy())
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        flame_mask = self.get_flame_mask(frame)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        flame_mask = cv2.morphologyEx(flame_mask, cv2.MORPH_OPEN, kernel)
        flame_mask = cv2.bitwise_and(flame_mask, self.bg_subtractor.apply(frame))
        contours, _ = cv2.findContours(flame_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        output_frame = frame.copy()
        fire_detected = False

        for cnt in contours:
            flame_type = self.is_real_flame(cnt, hsv)

            # Ignore weak "dancing tip" regions: too many false positives alone
            if flame_type == "dancing_tip" or flame_type is None:
                continue

            x, y, w, h = cv2.boundingRect(cnt)
            color = self.flame_colors[flame_type]
            label = "FIRE" if flame_type == "white_hot" else flame_type.replace('_', ' ').upper()
            cv2.rectangle(output_frame, (x, y), (x + w, y + h), color, 2)
            cv2.putText(output_frame, label, (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            fire_detected = True

        if fire_detected:
            cv2.putText(output_frame, "!!! FIRE ALERT !!!", (50, 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)

        return output_frame, flame_mask, fire_detected


class FireAlarm:
    """Turns per-frame detections into stable ON/OFF commands sent over MQTT."""

    def __init__(self, broker, port, topic, confirm_frames, clear_seconds):
        self.topic = topic
        self.confirm_frames = confirm_frames
        self.clear_seconds = clear_seconds
        self.consecutive = 0
        self.alarm_on = False
        self.last_fire_time = 0.0
        self.last_refresh = 0.0
        self.client = None

        if broker and mqtt is None:
            print("paho-mqtt is not installed: running without MQTT (pip install paho-mqtt)")
        elif broker:
            if hasattr(mqtt, "CallbackAPIVersion"):  # paho-mqtt 2.x
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            else:  # paho-mqtt 1.x
                self.client = mqtt.Client()
            self.client.connect(broker, port, keepalive=60)
            self.client.loop_start()
            print(f"Connected to MQTT broker {broker}:{port}, topic '{topic}'")

    def publish(self, message):
        print(f"[ALARM] {message}")
        if self.client:
            self.client.publish(self.topic, message, qos=1)

    def update(self, fire_detected):
        now = time.time()
        self.consecutive = self.consecutive + 1 if fire_detected else 0
        if fire_detected:
            self.last_fire_time = now

        if not self.alarm_on and self.consecutive >= self.confirm_frames:
            self.alarm_on = True
            self.last_refresh = now
            self.publish("ON")
        elif self.alarm_on and fire_detected and now - self.last_refresh > 10:
            # Refresh: the ESP32 switches itself off after 60 s without an "ON"
            self.last_refresh = now
            self.publish("ON")
        elif self.alarm_on and now - self.last_fire_time > self.clear_seconds:
            self.alarm_on = False
            self.publish("OFF")

    def close(self):
        if self.client:
            if self.alarm_on:
                self.publish("OFF")
            self.client.loop_stop()
            self.client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Flame detection on an ESP32-CAM stream")
    parser.add_argument("--stream", default="http://192.168.137.57:81/stream",
                        help="ESP32-CAM stream URL (or 0 for the PC webcam)")
    parser.add_argument("--broker", default=None, help="MQTT broker IP (omit to run without MQTT)")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--topic", default="home/fire", help="Must match topic_sub in esp32_relay.ino")
    parser.add_argument("--confirm-frames", type=int, default=5,
                        help="Consecutive fire frames needed before raising the alarm")
    parser.add_argument("--clear-seconds", type=float, default=5.0,
                        help="Seconds without fire before sending OFF")
    parser.add_argument("--headless", action="store_true", help="Do not open display windows")
    args = parser.parse_args()

    stream = int(args.stream) if args.stream.isdigit() else args.stream
    print(f"Starting flame detection on {stream}")
    detector = FlameDetector(stream)
    alarm = FireAlarm(args.broker, args.port, args.topic, args.confirm_frames, args.clear_seconds)

    try:
        while True:
            frame = detector.read_frame()
            if frame is None:
                continue
            result_frame, flame_mask, fire_detected = detector.process_frame(frame)
            alarm.update(fire_detected)

            if not args.headless:
                cv2.imshow("Flame Detection", result_frame)
                cv2.imshow("Flame Mask", flame_mask)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    except KeyboardInterrupt:
        print("Program stopped by user")
    finally:
        alarm.close()
        detector.cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
