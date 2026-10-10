import os
import queue
import threading
import tkinter as tk
import sys
import tempfile
from pathlib import Path
from tkinter import messagebox, ttk
from types import SimpleNamespace
from .printsetu_agent import CONFIG, activate, list_printers, load_config, run, WindowsPrinter


class AgentWindow:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        root.title("PrintSetu | Shop Connector")
        root.geometry("640x740")
        root.minsize(540, 680)
        self.tray = None
        root.configure(background="#f4f6f8")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f6f8")
        style.configure("TLabel", background="#f4f6f8", font=("Segoe UI", 11))
        style.configure("TButton", font=("Segoe UI", 11), padding=9)
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="PrintSetu", font=("Segoe UI", 24, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Shop Connector", font=("Segoe UI", 13)).pack(anchor="w", pady=(0, 18))
        self.server = tk.StringVar(value="http://127.0.0.1:8000")
        if CONFIG.exists():
            try:
                self.server.set(load_config()["server"])
            except (ValueError, KeyError):
                pass
        self.code = tk.StringVar()
        self.mode = tk.StringVar(value="mock")
        self.printer = tk.StringVar()
        self.status = tk.StringVar(value="Connected to shop" if CONFIG.exists() else "Awaiting shop connection")
        for label, variable in (("Server address", self.server), ("Shop connection code", self.code)):
            ttk.Label(frame, text=label).pack(anchor="w", pady=(8, 4))
            ttk.Entry(frame, textvariable=variable, font=("Segoe UI", 12)).pack(fill="x")
        self.connect_button = ttk.Button(frame, text="Connect shop", command=self.connect)
        self.connect_button.pack(anchor="w", pady=12)
        ttk.Separator(frame).pack(fill="x", pady=8)
        modes = ttk.Frame(frame)
        modes.pack(fill="x", pady=8)
        ttk.Radiobutton(modes, text="Demo mock (no physical printing)", variable=self.mode, value="mock").pack(anchor="w")
        ttk.Radiobutton(modes, text="Windows printer", variable=self.mode, value="windows").pack(anchor="w", pady=6)
        ttk.Label(frame, text="Printer").pack(anchor="w")
        self.printer_menu = ttk.Combobox(frame, textvariable=self.printer, values=list_printers(), state="readonly")
        self.printer_menu.pack(fill="x", pady=6)
        ttk.Button(frame, text="Refresh printers", command=lambda: self.printer_menu.configure(values=list_printers())).pack(anchor="w")
        self.test_button = ttk.Button(frame, text="Test Print", command=self.test_print)
        self.test_button.pack(anchor="w", pady=6)
        self.startup = tk.BooleanVar(value=self.startup_enabled())
        ttk.Checkbutton(frame, text="Open at Windows sign-in", variable=self.startup, command=self.set_startup).pack(anchor="w", pady=6)
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=8)
        self.start_button = ttk.Button(controls, text="Start listening", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(frame, textvariable=self.status, wraplength=530).pack(anchor="w", pady=8)
        self.activity = tk.Text(frame, height=6, font=("Consolas", 10), state="disabled", wrap="word")
        self.activity.pack(fill="both", expand=True)
        root.after(150, self.update)
        root.protocol("WM_DELETE_WINDOW", self.close)
        ttk.Button(frame, text="Minimize to tray", command=self.minimize).pack(anchor="w", pady=6)

    def startup_enabled(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                return bool(winreg.QueryValueEx(key, 'PrintSetu')[0])
        except OSError:
            return False

    def set_startup(self):
        if not getattr(sys, 'frozen', False):
            self.startup.set(False)
            messagebox.showinfo('Installed application', 'Use the packaged Windows application to enable sign-in startup.')
            return
        try:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                if self.startup.get():
                    winreg.SetValueEx(key, 'PrintSetu', 0, winreg.REG_SZ, f'"{sys.executable}"')
                else:
                    try: winreg.DeleteValue(key, 'PrintSetu')
                    except FileNotFoundError: pass
        except OSError as exc:
            messagebox.showerror('Startup setting', str(exc))

    def minimize(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            if self.tray is None:
                icon = Image.new('RGB', (64, 64), '#176b57')
                draw = ImageDraw.Draw(icon)
                draw.rectangle((14, 23, 50, 46), outline='white', width=4)
                draw.rectangle((22, 9, 42, 27), fill='white')
                self.tray = pystray.Icon('PrintSetu', icon, 'PrintSetu Connector', menu=pystray.Menu(pystray.MenuItem('Open', lambda *_: self.events.put(('show', ''))), pystray.MenuItem('Quit', lambda *_: self.events.put(('quit', '')))))
                self.tray.run_detached()
            self.root.withdraw()
        except Exception as exc:
            messagebox.showerror('Tray unavailable', str(exc))

    def test_print(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('Stop listener', 'Stop listening before running a test print.')
            return
        if self.mode.get() == 'mock':
            self.status.set('MOCK test only - no physical printer used')
            return
        if not self.printer.get():
            messagebox.showerror('Select printer', 'Select an installed Windows printer first.')
            return
        if not messagebox.askyesno('Test Print', 'Submit one test page to the selected physical printer?'):
            return
        printer = self.printer.get()
        self.test_button.configure(state='disabled')
        def task():
            try:
                import pymupdf
                with tempfile.TemporaryDirectory(prefix='printsetu-test-') as directory:
                    path = Path(directory) / 'test.pdf'
                    with pymupdf.open() as pdf:
                        page = pdf.new_page(); page.insert_text((60, 80), 'PrintSetu connection test', fontsize=22); page.insert_text((60, 130), 'A spooler submission does not confirm physical completion.', fontsize=11); pdf.save(path)
                    result = WindowsPrinter(printer).print_pdf(path, {'paper_size':'A4','color_mode':'bw','duplex':False,'copies':1,'page_range':'all'})
                self.events.put(('test', 'Test submitted to spooler: ' + result['spooler_job_id'] + '. Physical completion unconfirmed.'))
            except Exception as exc:
                self.events.put(('test', 'Test failed: ' + str(exc)))
        threading.Thread(target=task, daemon=True).start()

    def connect(self):
        if not self.code.get().strip():
            messagebox.showerror("Connection code", "Enter the code from your shop dashboard.")
            return
        server = self.server.get().strip().rstrip("/")
        code = self.code.get().strip().upper()
        self.connect_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.status.set("Connecting...")
        def task():
            try:
                activate(SimpleNamespace(server=server, code=code, name=os.environ.get("COMPUTERNAME", "Shop PC")))
                self.events.put(("connected", "Connected to shop. Ready to start."))
            except Exception as exc:
                self.events.put(("connection_error", str(exc)))
        threading.Thread(target=task, daemon=True).start()

    def start(self):
        if not CONFIG.exists():
            messagebox.showerror("Connect shop", "Connect this computer to your shop first.")
            return
        if self.mode.get() == "windows" and not self.printer.get():
            messagebox.showerror("Select printer", "Select an installed Windows printer.")
            return
        self.stop_event.clear()
        args = SimpleNamespace(mode=self.mode.get(), printer=self.printer.get(), poll_seconds=3)
        self.start_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Listening - MOCK, no physical printing" if args.mode == "mock" else "Listening - Windows printer")
        def task():
            try:
                run(args, self.stop_event, lambda line: self.events.put(("log", line)))
            except Exception as exc:
                self.events.put(("log", str(exc)))
            finally:
                self.events.put(("stopped", "Listener stopped"))
        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.status.set("Stopping after the current request/job...")
        self.stop_button.configure(state="disabled")

    def update(self):
        while not self.events.empty():
            kind, text = self.events.get_nowait()
            if kind == 'show':
                self.root.deiconify(); self.root.lift(); continue
            if kind == 'quit':
                self.close(); continue
            if kind == 'test':
                self.test_button.configure(state='normal')
                self.status.set(text)
            if kind in {"connected", "connection_error", "stopped"}:
                self.status.set(text)
                self.connect_button.configure(state="normal")
                self.start_button.configure(state="normal")
            if kind == "connected":
                self.code.set("")
            if kind == "stopped":
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
            self.activity.configure(state="normal")
            self.activity.insert("end", text + "\n")
            self.activity.see("end")
            self.activity.configure(state="disabled")
        self.root.after(150, self.update)

    def close(self):
        if self.worker and self.worker.is_alive():
            self.stop()
            self.root.after(200, self.close_when_stopped)
        else:
            if self.tray: self.tray.stop()
            self.root.destroy()

    def close_when_stopped(self):
        if self.worker.is_alive():
            self.root.after(200, self.close_when_stopped)
        else:
            if self.tray: self.tray.stop()
            self.root.destroy()


def main():
    root = tk.Tk()
    AgentWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
