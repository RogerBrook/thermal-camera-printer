#!/usr/bin/env python3
import os
import glob
import re
import time
import threading
import serial
from signal import pause
from gpiozero import Button
from picamera2 import Picamera2
from PIL import Image, ImageEnhance, ImageDraw, ImageFont
import st7789  # Ensure this library is installed and configured for your display

# ----- Settings for file saving -----
SAVE_FOLDER = '/home/roger/captured_images'
os.makedirs(SAVE_FOLDER, exist_ok=True)

def get_next_filename(prefix="polarock", ext="jpeg"):
    """
    Scans SAVE_FOLDER for files matching prefix*.ext and returns the next filename.
    """
    pattern = re.compile(rf"{prefix}(\d+)\.{ext}$")
    files = glob.glob(os.path.join(SAVE_FOLDER, f"{prefix}*.{ext}"))
    max_num = 0
    for file in files:
        basename = os.path.basename(file)
        match = pattern.match(basename)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num
    return os.path.join(SAVE_FOLDER, f"{prefix}{max_num+1}.{ext}")

# ----- Global variables for camera sharing and settings -----
camera_instance = None
capture_lock = threading.Lock()

# ISO and shutter speed settings
iso_options = [100, 200, 400, 800, 1600, 3000]
# Shutter speeds in microseconds; these are approximate values
shutter_options = [1000000, 500000, 250000, 125000, 66667, 33333, 16667, 8000, 4000, 2000, 1000, 500, 250]
shutter_display = ["1s", "1/2", "1/4", "1/8", "1/15", "1/30", "1/60", "1/125", "1/250", "1/500", "1/1000", "1/2000", "1/4000"]

# Start with ISO 100 and shutter speed 1/30 (approx 33333 µs)
iso_index = 0
shutter_index = 5

def update_camera_controls():
    """
    Update the shared camera's controls for ISO and shutter speed.
    For Picamera2 (using libcamera), we assume ISO 100 corresponds to an analogue gain of 1.0.
    """
    global camera_instance, iso_index, shutter_index
    if camera_instance is not None:
        try:
            gain = iso_options[iso_index] / 100.0  # Example: ISO 200 -> 2.0 gain
            camera_instance.set_controls({
                "ExposureTime": shutter_options[shutter_index],
                "AnalogueGain": gain
            })
            print(f"Updated camera controls: ISO {iso_options[iso_index]} (Gain {gain}), Shutter {shutter_display[shutter_index]}")
        except Exception as e:
            print("Failed to update camera controls:", e)

def iso_down():
    global iso_index
    if iso_index > 0:
        iso_index -= 1
        update_camera_controls()

def iso_up():
    global iso_index
    if iso_index < len(iso_options) - 1:
        iso_index += 1
        update_camera_controls()

def shutter_down():
    global shutter_index
    if shutter_index > 0:
        shutter_index -= 1
        update_camera_controls()

def shutter_up():
    global shutter_index
    if shutter_index < len(shutter_options) - 1:
        shutter_index += 1
        update_camera_controls()

def run_live_camera():
    """
    Starts the live camera feed on the ST7789 display and overlays current ISO and shutter speed.
    Shares the camera instance for still capture.
    """
    global camera_instance, iso_index, shutter_index
    # Initialize the ST7789 LCD display
    disp = st7789.ST7789(
        height=240,             # LCD height in pixels
        width=320,              # LCD width in pixels
        rotation=0,             # Adjust rotation as needed
        port=0,                 # SPI bus index (usually 0)
        cs=0,                   # Chip Select (CE0)
        dc=9,                   # Data/Command pin (GPIO 9)
        backlight=25,           # Backlight control pin (GPIO 25)
        spi_speed_hz=40000000   # SPI clock speed (adjust if needed)
    )
    
    # Initialize and configure the camera for a live preview at 320x240
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
    picam2.configure(config)
    picam2.start()
    camera_instance = picam2  # Share the camera instance

    # Set initial controls
    update_camera_controls()

    # Prepare a font for overlaying text (using default PIL font)
    font = ImageFont.load_default()

    print("Live camera started.")
    try:
        while True:
            with capture_lock:
                frame = picam2.capture_array()
            if frame is not None:
                img = Image.fromarray(frame)
                # Overlay text at the bottom of the image
                draw = ImageDraw.Draw(img)
                text = f"ISO: {iso_options[iso_index]}  Shutter: {shutter_display[shutter_index]}"
                draw.text((5, img.height - 20), text, font=font, fill=255)
                disp.display(img)
                print("Live feed frame captured with shape:", frame.shape)
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    finally:
        picam2.stop()
        print("Live camera stopped.")

def capture_image_and_print():
    """
    Captures a still image using the shared camera instance, saves it with an incremental filename,
    preprocesses it for thermal printing, converts it to ESC/POS binary data, and sends it to the printer.
    """
    global camera_instance, capture_lock
    if camera_instance is None:
        print("Error: Camera is not available.")
        return

    with capture_lock:
        print("Capturing still image for printing...")
        frame = camera_instance.capture_array()
    if frame is None:
        print("Failed to capture still image.")
        return

    output_path = get_next_filename()
    img = Image.fromarray(frame)
    img.save(output_path)
    print("Image captured and saved as", output_path)

    processed_img = preprocess_image(img)
    escpos_data = image_to_escpos(processed_img)

    printer_port = '/dev/serial0'
    baud_rate = 9600
    try:
        ser = serial.Serial(printer_port, baud_rate)
        print("Sending data to printer...")
        ser.write(escpos_data)
        ser.close()
        print("Image printed successfully.")
    except Exception as e:
        print("Error printing image:", e)

def preprocess_image(img):
    """
    Preprocesses the image for thermal printing:
      - Resizes to 384 pixels wide.
      - Enhances contrast and sharpness.
      - Converts to grayscale and binarizes the image.
    """
    width = 384
    aspect_ratio = img.height / img.width
    height = int(width * aspect_ratio)
    img = img.resize((width, height))
    img = ImageEnhance.Contrast(img).enhance(3.0)
    img = ImageEnhance.Sharpness(img).enhance(3.0)
    img = img.convert('L')
    img = img.point(lambda x: 0 if x < 128 else 255, '1')
    return img

def image_to_escpos(img):
    """
    Converts a binarized PIL Image (mode '1') into ESC/POS binary data for printing.
    """
    width, height = img.size
    pixels = img.load()
    escpos_data = b"\x1B\x40"
    for y in range(height):
        row_data = b""
        for x in range(0, width, 8):
            byte = 0
            for bit in range(8):
                if x + bit < width:
                    if pixels[x + bit, y] == 0:
                        byte |= (1 << (7 - bit))
            row_data += bytes([byte])
        escpos_data += b'\x1D\x76\x30\x00'
        escpos_data += bytes([width // 8, 0, 1, 0])
        escpos_data += row_data
    escpos_data += b'\x1D\x56\x00'
    return escpos_data

def button_pressed():
    """
    Called when the print button (GPIO2) is pressed.
    """
    print("Print button pressed! Initiating capture and print sequence...")
    capture_image_and_print()

# ----- Set up buttons -----
# Print capture button on GPIO2 (wired from GPIO2 to GND, so leave as pull_up=True for fixed pull-up)
print_button = Button(2, pull_up=True, bounce_time=0.1)
print_button.when_pressed = button_pressed

# ISO adjustment buttons (wired from 3.3V to the GPIO, so use internal pull-down)
iso_down_btn = Button(5, pull_up=False, bounce_time=0.1)
iso_up_btn = Button(6, pull_up=False, bounce_time=0.1)
iso_down_btn.when_pressed = iso_down
iso_up_btn.when_pressed = iso_up

# Shutter speed adjustment buttons (wired from 3.3V, so use internal pull-down)
shutter_down_btn = Button(17, pull_up=False, bounce_time=0.1)
shutter_up_btn = Button(13, pull_up=False, bounce_time=0.1)
shutter_down_btn.when_pressed = shutter_down
shutter_up_btn.when_pressed = shutter_up

# ----- Start the live camera thread -----
camera_thread = threading.Thread(target=run_live_camera, daemon=True)
camera_thread.start()

# Keep the main thread running so that button events are processed
pause()
