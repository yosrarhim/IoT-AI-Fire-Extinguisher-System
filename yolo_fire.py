"""YOLOv3-Tiny fire detector (Stage 1 of the AI pipeline).

YOLOv3-Tiny is a lightweight CNN object detector: 13 convolutional layers and two
detection heads (13x13 and 26x26 grids), small enough to run in real time on a laptop
CPU on the ESP32-CAM video stream. It is loaded with the OpenCV DNN module, so no
deep-learning framework is needed at inference time.

The model is a 1-class ("fire") YOLOv3-Tiny: see models/yolov3-tiny-fire.cfg and
docs/TRAINING.md to train the weights on a fire image dataset.
"""

import cv2
import numpy as np


class YoloV3TinyFireDetector:
    def __init__(self, cfg_path, weights_path, input_size=416,
                 conf_threshold=0.4, nms_threshold=0.4, class_names=("fire",)):
        self.net = cv2.dnn.readNetFromDarknet(cfg_path, weights_path)
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.output_layers = self.net.getUnconnectedOutLayersNames()
        self.input_size = input_size
        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold
        self.class_names = class_names

    def detect(self, frame):
        """Return a list of ((x, y, w, h), confidence) fire boxes in pixel coordinates."""
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (self.input_size, self.input_size),
                                     swapRB=True, crop=False)
        self.net.setInput(blob)
        outputs = self.net.forward(self.output_layers)

        boxes, confidences = [], []
        for output in outputs:
            for det in output:
                # det = [cx, cy, bw, bh, objectness, class scores...] (normalized)
                scores = det[5:]
                class_id = int(np.argmax(scores))
                confidence = float(det[4] * scores[class_id])
                if confidence < self.conf_threshold:
                    continue
                cx, cy, bw, bh = det[0] * w, det[1] * h, det[2] * w, det[3] * h
                boxes.append([int(cx - bw / 2), int(cy - bh / 2), int(bw), int(bh)])
                confidences.append(confidence)

        keep = cv2.dnn.NMSBoxes(boxes, confidences, self.conf_threshold, self.nms_threshold)
        return [(tuple(boxes[i]), confidences[i]) for i in np.array(keep).flatten()]

    def draw(self, frame, detections):
        for (x, y, bw, bh), conf in detections:
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), (255, 0, 255), 2)
            cv2.putText(frame, f"YOLO fire {conf:.2f}", (x, y + bh + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 0, 255), 2)
