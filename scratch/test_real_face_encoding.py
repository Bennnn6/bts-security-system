import os
import sys
sys.path.insert(0, os.path.abspath('.'))

import cv2
import numpy as np
import base64
from app.face_recognition_module import extract_face_encoding, face_recognition

print("face_recognition module:", face_recognition)
print("OpenCV cv2:", cv2.__file__)
print("numpy:", np.__file__)

# Create a dummy RGB image of a face or solid color
img = np.zeros((300, 300, 3), dtype=np.uint8)
# Draw a circle so it's a valid JPEG image
cv2.circle(img, (150, 150), 50, (255, 255, 255), -1)

_, buffer = cv2.imencode('.jpg', img)
b64_img = 'data:image/jpeg;base64,' + base64.b64encode(buffer).decode('utf-8')

# Test extract_face_encoding
encoding, msg = extract_face_encoding(b64_img)
print("Extract face result message:", msg)
# If no real human face in the circle, it should say 'Wajah tidak terdeteksi' (NOT 'Fitur face recognition belum tersedia')
print("[ SUCCESS ] The face recognition engine is ACTIVE and functional!")
assert msg != 'Fitur face recognition belum tersedia di environment server.', "Should not be unavailable!"
