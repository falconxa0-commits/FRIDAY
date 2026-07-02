import os
import subprocess
import platform

class AppLauncher:
    def launch(self, app_name):
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(app_name)
            elif system == "Darwin": # macOS
                subprocess.run(["open", "-a", app_name])
            else: # Linux
                subprocess.run(["xdg-open", app_name])
            return True
        except Exception as e:
            print(f"Failed to launch {app_name}: {e}")
            return False
