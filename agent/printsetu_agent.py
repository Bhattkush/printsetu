import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import requests
import base64
import sqlite3
from .windows_printer import WindowsPrinter, capabilities

CONFIG = Path.home() / ".printsetu-agent.json"


def save_config(data: dict) -> None:
    if os.name != 'nt':
        raise RuntimeError('Device credentials require Windows secure storage')
    import win32crypt
    protected = dict(data)
    protected['protected_credential'] = base64.b64encode(win32crypt.CryptProtectData(protected.pop('credential').encode(), 'PrintSetu device', None, None, None, 0)).decode()
    temporary = CONFIG.with_suffix('.tmp')
    temporary.write_text(json.dumps(protected, indent=2), encoding='utf-8')
    temporary.replace(CONFIG)


def load_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit("Agent is not activated. Run: python -m agent.printsetu_agent activate --server http://127.0.0.1:8000 --code CODE")
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    if 'protected_credential' in data:
        import win32crypt
        data['credential'] = win32crypt.CryptUnprotectData(base64.b64decode(data['protected_credential']), None, None, None, 0)[1].decode()
    else:
        save_config(data)
    return data


def headers(cfg: dict) -> dict:
    return {"Authorization": f"Bearer {cfg['credential']}"}


def list_printers() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import win32print
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [p[2] for p in win32print.EnumPrinters(flags)]
    except Exception as exc:
        print(f"Could not read Windows printers: {exc}")
        return []


class MockPrinter:
    label = "MOCK - no physical printing"

    def print_pdf(self, path: Path, options: dict) -> dict:
        print(f"[MOCK MODE] Would print {path.name} with {options}. No physical printer was used.")
        time.sleep(1)
        return {"status": "COMPLETED", "spooler_job_id": "mock-no-spooler"}




def activate(args):
    response = requests.post(f"{args.server}/api/agent/activate", json={"code": args.code, "name": args.name}, timeout=20)
    response.raise_for_status()
    data = response.json()
    data["server"] = args.server.rstrip("/")
    save_config(data)
    print(f"Activated device {data['device_id']} for shop {data['shop_id']}. Credential stored at {CONFIG}.")


def printers(_args):
    found = list_printers()
    if not found:
        print("No Windows printers detected. Mock mode remains available for labelled development tests.")
    for name in found:
        print(name)


def post_status(cfg: dict, job_id: str, status: str, mode: str, **extra):
    body = {"status": status, "adapter_mode": mode, **extra}
    requests.post(f"{cfg['server']}/api/agent/jobs/{job_id}/status", json=body, headers=headers(cfg), timeout=20).raise_for_status()


def run(args, stop_event=None, log=print):
    cfg = load_config()
    adapter = MockPrinter() if args.mode == "mock" else WindowsPrinter(args.printer)
    printer_info = []
    if args.mode == 'windows':
        printer_info = [capabilities(args.printer)]
    registered = False
    log(f"PrintSetu agent running in {adapter.label} mode.")
    log("Mock mode never means a physical print succeeded." if args.mode == "mock" else "Real mode reports spooler submission; physical completion depends on printer/driver feedback.")
    def pause():
        if stop_event is not None:
            stop_event.wait(args.poll_seconds)
        else:
            time.sleep(args.poll_seconds)
    journal = sqlite3.connect(str(Path.home() / '.printsetu-jobs.db'))
    journal.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, shop TEXT, result TEXT)')
    journal.commit()
    while stop_event is None or not stop_event.is_set():
        job_id = None
        try:
            if not registered:
                requests.post(
                    f"{cfg['server']}/api/agent/connection",
                    json={"mode": args.mode, "printer": args.printer, "printers": printer_info},
                    headers=headers(cfg), timeout=20,
                ).raise_for_status()
                registered = True
            for pending_id, result in journal.execute('SELECT id, result FROM jobs WHERE shop = ? AND result IS NOT NULL', (cfg['shop_id'],)).fetchall():
                state = json.loads(result)
                post_status(cfg, pending_id, state['status'], state['mode'], spooler_job_id=state.get('spooler_job_id'), error=state.get('error'))
                journal.execute('UPDATE jobs SET result = NULL WHERE id = ?', (pending_id,)); journal.commit()
            next_resp = requests.get(f"{cfg['server']}/api/agent/jobs/next", headers=headers(cfg), timeout=30)
            next_resp.raise_for_status()
            job = next_resp.json()["job"]
            if not job:
                pause()
                continue
            job_id = job["id"]
            if journal.execute('SELECT id FROM jobs WHERE id = ?', (job_id,)).fetchone():
                raise RuntimeError('Job has already been attempted on this computer; reconcile the spooler before retrying')
            journal.execute('INSERT INTO jobs (id,shop) VALUES (?,?)', (job_id,cfg['shop_id'])); journal.commit()
            post_status(cfg, job_id, "PRINTING", args.mode)
            with tempfile.TemporaryDirectory(prefix="printsetu-") as tmp:
                local_pdf = Path(tmp) / f"{job['document_id']}.pdf"
                doc_resp = requests.get(f"{cfg['server']}{job['download_url']}", headers=headers(cfg), timeout=60, stream=True)
                doc_resp.raise_for_status()
                size = 0
                with doc_resp, local_pdf.open('wb') as out:
                    for chunk in doc_resp.iter_content(65536):
                        size += len(chunk)
                        if size > 25 * 1024 * 1024:
                            raise RuntimeError('Document exceeds the agent size limit')
                        out.write(chunk)
                result = adapter.print_pdf(local_pdf, job["options"])
                journal.execute('UPDATE jobs SET result = ? WHERE id = ?', (json.dumps({**result, 'mode': args.mode}),job_id)); journal.commit()
                post_status(cfg, job_id, result["status"], args.mode, spooler_job_id=result.get("spooler_job_id"))
                journal.execute('UPDATE jobs SET result = NULL WHERE id = ?', (job_id,)); journal.commit()
                log(f"Job {job_id} reported as {result['status']}; temporary file cleanup completed.")
        except KeyboardInterrupt:
            log("Stopping agent.")
            journal.close()
            return
        except Exception as exc:
            log(f"Agent error: {exc}")
            if job_id:
                try:
                    saved = journal.execute('SELECT result FROM jobs WHERE id = ?', (job_id,)).fetchone()
                    if saved and saved[0]:
                        log('Spooler result saved. Status delivery will retry without printing again.')
                        pause()
                        continue
                    journal.execute('UPDATE jobs SET result = ? WHERE id = ?', (json.dumps({'status':'FAILED','mode':args.mode,'error':str(exc)}),job_id)); journal.commit()
                    post_status(cfg, job_id, "FAILED", args.mode, error=str(exc))
                    journal.execute('UPDATE jobs SET result = NULL WHERE id = ?', (job_id,)); journal.commit()
                except Exception as status_error:
                    log(f"Could not report failure: {status_error}")
            pause()
    journal.close()


def main():
    parser = argparse.ArgumentParser(prog="printsetu-agent")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("activate")
    p.add_argument("--server", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--name", default="Windows PC")
    p.set_defaults(func=activate)
    p = sub.add_parser("printers")
    p.set_defaults(func=printers)
    p = sub.add_parser("run")
    p.add_argument("--mode", choices=["mock", "windows"], default="mock")
    p.add_argument("--printer")
    p.add_argument("--poll-seconds", type=int, default=3)
    p.set_defaults(func=run)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

