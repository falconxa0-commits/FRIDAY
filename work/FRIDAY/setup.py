import os
import subprocess
import sys
import shutil

def install_deps():
    print("Installing Project FRIDAY dependencies...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("Dependencies installed successfully.")
    except Exception as e:
        print(f"Failed to install dependencies: {e}")

def init_env():
    if not os.path.exists(".env"):
        if os.path.exists(".env.example"):
            shutil.copy(".env.example", ".env")
            print(".env file created from .env.example.")
        else:
            print("Warning: .env.example not found. Please create .env manually.")
    else:
        print(".env file already exists.")

def main():
    print("--- Project FRIDAY Setup ---")
    init_env()
    install_deps()
    print("\nSetup finished. Please edit .env with your real API keys.")
    print("Then run 'python main.py' to start Friday.")

if __name__ == "__main__":
    main()
