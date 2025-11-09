import cv2
import numpy as np
from collections import deque

class FlameDetector:
    def __init__(self, stream_url='http://192.168.137.57:81/stream'):
        # Connect to ESP32-CAM stream
        self.cap = cv2.VideoCapture(stream_url)
       
        # ESP32-CAM often needs these settings for stable stream
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap.set(cv2.CAP_PROP_FPS, 10)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
       
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

    def get_flame_mask(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        combined_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        for name, profile in self.flame_profiles.items():
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
        if len(self.frame_buffer) < 2:
            return 0
        x, y, w, h = cv2.boundingRect(contour)
        motion_scores = []
        for i in range(1, min(4, len(self.frame_buffer))):
            prev = cv2.cvtColor(self.frame_buffer[-i], cv2.COLOR_BGR2GRAY)
            curr = cv2.cvtColor(self.frame_buffer[-1], cv2.COLOR_BGR2GRAY)
            prev_roi = prev[y:y+h, x:x+w]
            curr_roi = curr[y:y+h, x:x+w]
            if prev_roi.size == 0 or curr_roi.size == 0:
                continue
            diff = cv2.absdiff(prev_roi, curr_roi)
            motion_scores.append(np.mean(diff))
        return np.mean(motion_scores) if motion_scores else 0

    def is_real_flame(self, contour, frame):
        features = self.analyze_flame_characteristics(contour)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        for name, profile in self.flame_profiles.items():
            color_mask = cv2.inRange(hsv, profile['lower'], profile['upper'])
            contour_mask = np.zeros_like(color_mask)
            cv2.drawContours(contour_mask, [contour], -1, 255, -1)
            overlap = cv2.bitwise_and(color_mask, contour_mask)
            color_coverage = np.sum(overlap) / (features['area'] + 1e-5)
            if (color_coverage > 0.6 and
                features['area'] > profile['min_area'] and
                features['circularity'] < profile['max_circularity'] and
                features['motion_score'] > 5.0):
                return name
        return None

    def process_frame(self, frame):
        self.frame_buffer.append(frame.copy())
        flame_mask = self.get_flame_mask(frame)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        flame_mask = cv2.morphologyEx(flame_mask, cv2.MORPH_OPEN, kernel)
        flame_mask = cv2.bitwise_and(flame_mask, self.bg_subtractor.apply(frame))
        contours, _ = cv2.findContours(flame_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        output_frame = frame.copy()
        fire_detected = False

        for cnt in contours:
            flame_type = self.is_real_flame(cnt, frame)

            # Ignore dancing_tip
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

        return output_frame, flame_mask

    def run(self):
        while True:
            ret, frame = self.cap.read()
            if not ret:
                print("Error: Could not read frame from stream. Reconnecting...")
                self.cap.release()
                self.cap = cv2.VideoCapture('http://192.168.137.57:81/stream')
                cv2.waitKey(1000)  # Wait before trying again
                continue
           
            # Resize for consistent processing
            frame = cv2.resize(frame, (640, 480))
           
            result_frame, flame_mask = self.process_frame(frame)
            cv2.imshow("Flame Detection", result_frame)
            cv2.imshow("Flame Mask", flame_mask)
           
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    print("Starting Flame Detection with ESP32-CAM")
    print("Make sure your ESP32-CAM is connected to the same network")
   
    # Initialize with default ESP32-CAM stream URL
    detector = FlameDetector()
   
    # Alternative URLs you can use:
    # detector = FlameDetector('http://192.168.1.101:81/stream')  # Different IP
    # detector = FlameDetector('rtsp://192.168.1.100:8554/mjpeg/1')  # RTSP stream
   
    try:
        detector.run()
    except KeyboardInterrupt:
        print("Program stopped by user")
    except Exception as e:
        print(f"Error: {str(e)}")