import sys
import tkinter as tk
from agent.gui import AgentWindow, main


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        root = tk.Tk()
        root.withdraw()
        AgentWindow(root)
        root.update()
        root.destroy()
    else:
        main()
