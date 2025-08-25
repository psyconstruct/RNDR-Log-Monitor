# RNDR Log Monitor – improved with efficient log processing, Dark/Light Mode and customizable GUI
import tkinter as tk
from tkinter import messagebox, simpledialog
import subprocess
import threading
import re
import time
import os
import json
import webbrowser
from datetime import datetime
import sys
import shutil

# === CONFIGURATION ===
SETTINGS_FILE = os.path.expandvars(r"%LOCALAPPDATA%\\OtoyRndrNetwork\\monitor_settings.json")
DEFAULT_LOGFILE = os.path.expandvars(r"%LOCALAPPDATA%\\OtoyRndrNetwork\\rndr_log.txt")
TEST_LOGFILE = os.path.expandvars(r"%LOCALAPPDATA%\\OtoyRndrNetwork\\rndr_log_testing.txt")
AUTOSTART_SHORTCUT_PATH = os.path.join(os.getenv("APPDATA"), "Microsoft\\Windows\\Start Menu\\Programs\\Startup\\RNDRMonitor.lnk")

# === GLOBAL STATE ===
monitoring = False
monitor_thread = None
current_logfile = DEFAULT_LOGFILE
notified_started_hashes = set()
use_popup_notifications = False
check_interval = 30
completion_delay = 300
last_seen_render_start = None
job_started_sent = False
job_done_sent = False
last_position = 0
first_run = True
current_theme = "light"
NTFY_TOPIC = ""
autostart_enabled = False

# === SETTINGS LOAD/SAVE ===
def load_settings():
    global NTFY_TOPIC, DEFAULT_LOGFILE, TEST_LOGFILE, autostart_enabled
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
            NTFY_TOPIC = data.get("ntfy_topic", "")
            DEFAULT_LOGFILE = data.get("default_logfile", DEFAULT_LOGFILE)
            TEST_LOGFILE = data.get("test_logfile", TEST_LOGFILE)
            autostart_enabled = data.get("autostart_enabled", False)

def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump({
            "ntfy_topic": NTFY_TOPIC,
            "default_logfile": DEFAULT_LOGFILE,
            "test_logfile": TEST_LOGFILE,
            "autostart_enabled": autostart_enabled
        }, f)

def prompt_ntfy_topic():
    global NTFY_TOPIC

    def save_and_close():
        topic = entry.get().strip()
        if topic:
            global NTFY_TOPIC
            NTFY_TOPIC = topic
            save_settings()
            prompt.destroy()
        else:
            messagebox.showerror("Error", "You must enter a ntfy channel.")

    def cancel_and_close():
        prompt.destroy()

    def open_link(event):
        webbrowser.open_new("https://ntfy.sh/")

    prompt = tk.Toplevel()
    prompt.title("Set ntfy Channel")
    prompt.geometry("400x200")
    tk.Label(prompt, text="Enter your ntfy channel:").pack(pady=10)
    entry = tk.Entry(prompt, width=40)
    entry.pack(pady=5)
    if NTFY_TOPIC:
        entry.insert(0, NTFY_TOPIC)
        entry.config(fg="gray")

    link = tk.Label(prompt, text="To set an ntfy Channel open: https://ntfy.sh/", fg="blue", cursor="hand2")
    link.pack(pady=5)
    link.bind("<Button-1>", open_link)

    button_frame = tk.Frame(prompt)
    button_frame.pack(pady=10)
    tk.Button(button_frame, text="Save", command=save_and_close).pack(side=tk.LEFT, padx=5)
    tk.Button(button_frame, text="Cancel", command=cancel_and_close).pack(side=tk.LEFT, padx=5)

    prompt.grab_set()
    prompt.wait_window()

# === FUNCTIONALITY ===
def apply_theme(root, theme):
    bg = "#1e1e1e" if theme == "dark" else "#f0f0f0"
    fg = "#ffffff" if theme == "dark" else "#000000"
    root.configure(bg=bg)
    for widget in root.winfo_children():
        if isinstance(widget, (tk.Label, tk.Button, tk.Checkbutton, tk.Scale)):
            widget.configure(bg=bg, fg=fg)
        if isinstance(widget, tk.Frame):
            for child in widget.winfo_children():
                child.configure(bg=bg, fg=fg)

def send_ntfy_message(message):
    if not NTFY_TOPIC:
        messagebox.showwarning("Missing ntfy Channel", "Please set your ntfy channel first.")
        prompt_ntfy_topic()
        return
    if use_popup_notifications:
        messagebox.showinfo("Notification", message)
    else:
        try:
            subprocess.run(["curl", "-s", "-X", "POST", f"https://ntfy.sh/{NTFY_TOPIC}", "-d", message], check=True)
        except Exception as e:
            print(f"Error sending message: {e}")

def extract_hash(line):
    match = re.search(r"config hash: (\w+)", line)
    return match.group(1) if match else None

def monitor_log(status_label, toggle_button, slider):
    global monitoring, current_logfile, notified_started_hashes
    global last_seen_render_start, job_started_sent, job_done_sent
    global completion_delay, last_position, first_run

    while monitoring:
        completion_delay = slider.get()
        status_label.config(text="Status: ⏳ Monitoring log...")

        try:
            if os.path.exists(current_logfile):
                with open(current_logfile, "r", encoding="utf-8", errors="ignore") as file:
                    file.seek(last_position)
                    new_lines = file.readlines()
                    last_position = file.tell()

                for line in new_lines:
                    line = line.strip()

                    if "starting a new render job with config hash:" in line:
                        last_seen_render_start = time.time()
                        job_done_sent = False
                        hash_value = extract_hash(line)
                        if hash_value and hash_value not in notified_started_hashes:
                            if not first_run:
                                send_ntfy_message("🚀 New job started")
                                status_label.config(text="Status: 🚀 Job started")
                                job_started_sent = True
                            notified_started_hashes.add(hash_value)

                    elif "job failed with config hash:" in line:
                        if not first_run:
                            send_ntfy_message("❌ Job failed")
                            status_label.config(text="Status: ❌ Job failed")
                        job_started_sent = False
                        job_done_sent = False

                if job_started_sent and last_seen_render_start and (time.time() - last_seen_render_start) >= completion_delay:
                    send_ntfy_message("✅ Job completed!")
                    status_label.config(text="Status: ✅ Job completed")
                    job_done_sent = True
                    job_started_sent = False

                first_run = False

        except Exception as e:
            status_label.config(text="Status: ⚠️ Error reading log file")
            print(f"Monitoring error: {e}")

        time.sleep(check_interval)

def toggle_monitoring(status_label, toggle_button):
    global monitoring, monitor_thread, notified_started_hashes, last_position, first_run, job_started_sent, job_done_sent
    if not monitoring:
        monitoring = True
        toggle_button.config(text="🛑 Stop monitoring", bg="#B22222")
        notified_started_hashes.clear()
        last_position = 0
        first_run = True
        job_started_sent = False
        monitor_thread = threading.Thread(target=monitor_log, args=(status_label, toggle_button, delay_slider), daemon=True)
        monitor_thread.start()
    else:
        monitoring = False
        toggle_button.config(text="📡 Start monitoring", bg="#4CAF50")
        status_label.config(text="Status: ⏹️ Not active")

def set_logfile(use_test, logpath_label):
    global current_logfile, last_position
    if use_test:
        if not os.path.exists(TEST_LOGFILE):
            with open(TEST_LOGFILE, "w", encoding="utf-8") as f:
                f.write("")
        current_logfile = TEST_LOGFILE
    else:
        current_logfile = DEFAULT_LOGFILE
    last_position = 0
    logpath_label.config(text=f"Monitoring logfile:\n{current_logfile}")

def toggle_popup_mode(value):
    global use_popup_notifications
    use_popup_notifications = value

def update_delay(val, label):
    global completion_delay
    completion_delay = int(val)
    label.config(text=f"⏱️ Completion delay: {completion_delay} sec")

def update_interval(val, label):
    global check_interval
    check_interval = int(val)
    label.config(text=f"🔁 Refresh rate: {check_interval} sec")

def toggle_advanced(show, widgets):
    if show:
        for widget in widgets:
            widget.pack(pady=5)
        root.geometry("500x650")
    else:
        for widget in widgets:
            widget.pack_forget()
        root.geometry("500x300")

def toggle_theme(theme_var, root):
    global current_theme
    current_theme = "dark" if theme_var.get() else "light"
    apply_theme(root, current_theme)

def change_ntfy_channel():
    prompt_ntfy_topic()

def toggle_autostart(val):
    global autostart_enabled
    autostart_enabled = val
    if autostart_enabled:
        create_autostart_shortcut()
    else:
        remove_autostart_shortcut()
    save_settings()

def create_autostart_shortcut():
    import pythoncom
    from win32com.client import Dispatch

    target = sys.executable
    script_path = os.path.abspath(__file__)
    arguments = f'"{script_path}"'

    shell = Dispatch('WScript.Shell')
    shortcut = shell.CreateShortcut(AUTOSTART_SHORTCUT_PATH)
    shortcut.TargetPath = target
    shortcut.Arguments = arguments
    shortcut.WorkingDirectory = os.path.dirname(script_path)
    shortcut.IconLocation = target
    shortcut.Save()

def remove_autostart_shortcut():
    if os.path.exists(AUTOSTART_SHORTCUT_PATH):
        os.remove(AUTOSTART_SHORTCUT_PATH)

def create_gui():
    global delay_slider, root

    load_settings()

    if not NTFY_TOPIC:
        root = tk.Tk()
        root.withdraw()
        prompt_ntfy_topic()
        root.destroy()

    root = tk.Tk()
    root.title("RNDR Log Monitor")
    root.geometry("500x300")
    root.resizable(True, True)

    apply_theme(root, current_theme)

    tk.Label(root, text=f"Current ntfy channel: {NTFY_TOPIC}", font=("Arial", 10, "italic"), fg="gray").pack(pady=5)

    title_label = tk.Label(root, text="RNDR Log Monitor", font=("Arial", 16))
    title_label.pack(pady=10)

    logpath_label = tk.Label(root, text=f"Monitoring logfile:\n{current_logfile}", wraplength=460)
    logpath_label.pack(pady=5)

    status_label = tk.Label(root, text="Status: ⏹️ Not active", font=("Arial", 11))
    status_label.pack(pady=5)

    toggle_button = tk.Button(root, text="📡 Start monitoring", command=lambda: toggle_monitoring(status_label, toggle_button), bg="#4CAF50", fg="white", height=2, width=30)
    toggle_button.pack(pady=10)

    advanced_widgets = []

    use_test_var = tk.BooleanVar()
    test_checkbox = tk.Checkbutton(root, text="🔁 Use test logfile", variable=use_test_var, command=lambda: set_logfile(use_test_var.get(), logpath_label))
    advanced_widgets.append(test_checkbox)

    popup_var = tk.BooleanVar()
    popup_checkbox = tk.Checkbutton(root, text="💬 Use popup notifications", variable=popup_var, command=lambda: toggle_popup_mode(popup_var.get()))
    popup_checkbox.pack(pady=5)

    delay_label = tk.Label(root, text=f"⏱️ Completion delay: {completion_delay} sec")
    advanced_widgets.append(delay_label)

    delay_slider = tk.Scale(root, from_=120, to=900, orient=tk.HORIZONTAL, length=300, sliderlength=20, showvalue=True, variable=tk.IntVar(value=completion_delay), command=lambda val: update_delay(val, delay_label))
    advanced_widgets.append(delay_slider)

    interval_label = tk.Label(root, text=f"🔁 Refresh rate: {check_interval} sec")
    advanced_widgets.append(interval_label)

    interval_slider = tk.Scale(root, from_=1, to=120, orient=tk.HORIZONTAL, length=300, sliderlength=20, showvalue=True, variable=tk.IntVar(value=check_interval), command=lambda val: update_interval(val, interval_label))
    advanced_widgets.append(interval_slider)

    test_btn = tk.Button(root, text="📨 Send test message", command=lambda: send_ntfy_message("📨 Test message"))
    advanced_widgets.append(test_btn)

    change_channel_btn = tk.Button(root, text="🔄 Change ntfy channel", command=change_ntfy_channel)
    advanced_widgets.append(change_channel_btn)

    autostart_var = tk.BooleanVar(value=autostart_enabled)
    autostart_checkbox = tk.Checkbutton(root, text="🛠 Enable autostart", variable=autostart_var, command=lambda: toggle_autostart(autostart_var.get()))
    advanced_widgets.append(autostart_checkbox)

    show_advanced_var = tk.BooleanVar()
    tk.Checkbutton(root, text="⚙️ Advanced settings", variable=show_advanced_var, command=lambda: toggle_advanced(show_advanced_var.get(), advanced_widgets)).pack(pady=10)

    theme_var = tk.BooleanVar(value=(current_theme == "dark"))
    theme_checkbox = tk.Checkbutton(root, text="🌓 Enable dark mode", variable=theme_var, command=lambda: toggle_theme(theme_var, root))
    advanced_widgets.append(theme_checkbox)

    root.mainloop()

if __name__ == "__main__":
    create_gui()
