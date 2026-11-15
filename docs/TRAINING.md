# Training the YOLOv3-Tiny fire model

The detector uses a **1-class YOLOv3-Tiny** (`fire`) defined in [`models/yolov3-tiny-fire.cfg`](../models/yolov3-tiny-fire.cfg).
Training produces `yolov3-tiny-fire.weights`, which `flame_detector.py` loads with OpenCV DNN.

> **Fastest way:** open [`notebooks/train_yolov3_tiny_fire.ipynb`](../notebooks/train_yolov3_tiny_fire.ipynb) in Google Colab (free GPU) and run all cells. The steps below explain what it does.

## 1. Dataset

- Collect fire images in YOLO format (one `.txt` label per image, class `0` = fire), for example from public fire datasets on Roboflow Universe or Kaggle.
- **Add your own ESP32-CAM images**: low resolution, JPEG artifacts and the real lighting of the deployment site are what the model will see in production.
- Add **negative images** with no label (red or orange objects, lamps, LEDs, sunsets) to reduce false positives.
- Split about 80 % train / 20 % validation and list the image paths in `data/train.txt` and `data/valid.txt`.

```
data/
├── images/        # .jpg
├── labels/        # .txt, one line per box: 0 cx cy w h (normalized)
├── train.txt
└── valid.txt
```

## 2. Train with Darknet (GPU recommended, e.g. Google Colab)

```bash
git clone https://github.com/AlexeyAB/darknet && cd darknet
# set GPU=1 CUDNN=1 OPENCV=1 in the Makefile, then:
make

# pre-trained convolutional layers for YOLOv3-Tiny (transfer learning)
./darknet partial cfg/yolov3-tiny.cfg yolov3-tiny.weights yolov3-tiny.conv.15 15

./darknet detector train ../models/fire.data ../models/yolov3-tiny-fire.cfg yolov3-tiny.conv.15 -map
```

Training settings in the cfg: 416×416 input, `max_batches = 6000`, learning-rate steps at 4800 and 5400, 18 filters before each `[yolo]` layer (`(1 class + 5) × 3`).

## 3. Use the trained model

Copy `backup/yolov3-tiny-fire_best.weights` to `models/yolov3-tiny-fire.weights`, then:

```bash
python flame_detector.py --stream http://<ESP32-CAM-IP>:81/stream --broker <BROKER-IP> \
    --yolo-weights models/yolov3-tiny-fire.weights
```

## 4. Evaluate

- Report **mAP@0.5** on the validation set (`-map` flag above).
- Test the full system on real scenes and measure **false alarms per hour** and **time from flame to pump activation**: these numbers matter more for a safety system than mAP alone.
