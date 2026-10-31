import os, sys, tempfile
HOME = tempfile.mkdtemp()
os.environ["HOME"] = HOME
os.environ["WORKER_SECRET"] = "test-secret"
os.makedirs(f"{HOME}/Documents", exist_ok=True)
os.makedirs(f"{HOME}/.ssh", exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
