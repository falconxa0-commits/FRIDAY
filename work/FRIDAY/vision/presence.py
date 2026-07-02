import cv2
import time
import logging

class PresenceDetector:
    def __init__(self):
        # Using a more robust path check for the cascade
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    def detect_user(self):
        """
        Attempts real face detection using OpenCV.
        Note: Requires a working camera device.
        """
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return {"status": "error", "message": "Camera not accessible."}
                
            ret, frame = cap.read()
            if not ret:
                cap.release()
                return {"status": "error", "message": "Failed to capture frame."}
                
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.face_cascade.detectMultiScale(gray, 1.1, 4)
            
            cap.release()
            
            is_present = len(faces) > 0
            return {
                "status": "present" if is_present else "away",
                "faces_detected": len(faces),
                "timestamp": time.time()
            }
        except Exception as e:
            logging.error(f"Presence detection failed: {e}")
            return {"status": "error", "message": str(e)}

    def get_user_attention_level(self):
        return "Not implemented: Requires eye-tracking model."
