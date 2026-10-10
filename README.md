# PrintSetu

QR PDF printing with a React/TypeScript dashboard, FastAPI API, SQLAlchemy/Alembic database, private storage and a graphical Windows Shop Connector. Owners pair once; they do not run Python activation commands.

## Run Now

Dependencies and the database are prepared in `D:\qrprint`. Double-click **Start PrintSetu.cmd**. It starts both services, opens the owner dashboard using the current LAN address, and reuses existing PrintSetu servers to avoid port conflicts. Logs are in `.runtime`.

Local demo login: `owner@example.com` / `PrintSetuDemo123!`. This seeded account is local-only. Register your own shop at `/register` to try onboarding. Development OTPs are explicitly displayed; real email requires Resend credentials.

The current local `.env` enables **Razorpay test checkout**, requires payment, and disables unpaid demo printing. Restart FastAPI after configuration changes. New orders enter the print queue only after the server verifies a captured payment. Test payments are not live revenue. Existing unpaid demo orders are retained but cannot be collected with payment enforcement enabled.

## Fresh Windows Setup

Install Python 3.11+ and Node.js 24 LTS. PowerShell:

```powershell
cd D:\qrprint
py -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt -r agent\requirements.txt
cd frontend
npm ci
cd ..
Copy-Item .env.example .env
python -m alembic upgrade head
python -m backend.app.seed
.\'Start PrintSetu.cmd'
```

Do not overwrite an existing `.env` with secrets. The seed refuses production. This launcher is for local development, not a production service.

Manual alternative, two terminals left open:

```powershell
cd D:\qrprint
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

```powershell
cd D:\qrprint\frontend
npm run dev
```

On this PC open `http://127.0.0.1:5173/owner`. For phones, open `http://10.38.57.145:5173/owner` if that is still the PC's IPv4, then regenerate the QR. `127.0.0.1` in a QR points to the scanning phone, not this PC. Both phones and the PC need the same network without client isolation. One phone can display the QR for another phone to scan.

Allow Node on private networks in Windows Firewall when prompted. Phones need only port 5173: `/api` is proxied to FastAPI, avoiding localhost fetch failures. Never expose the development HTTP server to the Internet.

Error 10048 means another process owns port 8000. Identify it before stopping anything:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object LocalAddress, OwningProcess
Get-Process -Id YOUR_PROCESS_ID
```

Reuse an existing PrintSetu instance through the launcher. Only stop a recognized process when you want to restart: `Stop-Process -Id YOUR_PROCESS_ID`. Do not blindly kill another application.

## Try Without A Printer

1. Sign in, open Devices and click Connect computer.
2. Download/install `PrintSetu-Setup.exe` using Download Windows app. Source alternative: double-click `Open Shop Connector.cmd`.
3. Enter the server address and one-time code in the connector and click Connect shop. Pairing is saved with Windows DPAPI; codes expire after ten minutes.
4. Select Demo mock (no physical printing), then Start listening. Mock is forbidden in production.
5. Scan the shop QR, upload a PDF, choose settings and submit. Orders remain DEMO / UNPAID. Mock completion explicitly says no physical printing.

For real output select Windows mode and an installed printer. Use the confirmed Test Print action first. The GDI adapter renders selected pages with copies, paper, color and duplex driver settings. Unsupported options fail rather than silently changing. Real success means **SUBMITTED_TO_SPOOLER**, not proof that paper physically exited the device.

The connector includes printer refresh, saved pairing, optional Windows sign-in startup, tray controls and owner-side revocation. Keep it running on the shop PC; a browser cannot access USB printers directly. Automatic physical retry is deliberately disabled after uncertain submissions; check the spooler before manually handling a failed/stuck order.

## Razorpay Sandbox

Create Razorpay test credentials and edit `.env`:

```dotenv
PRINTSETU_RAZORPAY_MODE=test
PRINTSETU_RAZORPAY_KEY_ID=rzp_test_YOUR_KEY
PRINTSETU_RAZORPAY_KEY_SECRET=YOUR_TEST_SECRET
PRINTSETU_RAZORPAY_WEBHOOK_SECRET=YOUR_TEST_WEBHOOK_SECRET
PRINTSETU_PAYMENT_REQUIRED=true
PRINTSETU_ALLOW_UNPAID_LOCAL_PRINTING=false
PRINTSETU_DEMO_MODE=false
```

Restart FastAPI. Enable automatic payment capture in the Razorpay test dashboard. Configure a test webhook to a publicly reachable HTTPS `/api/payments/webhook`; subscribe to captured/payment-failed/order-paid/refund events. Set the same webhook secret in Razorpay and `.env`; it is separate from the API key secret. Razorpay cannot reach localhost. Use an isolated HTTPS staging deployment, not a public development dashboard.

The server creates durable payment intents, verifies checkout HMAC, independently fetches provider payment data, validates capture/order/amount/currency and only then queues printing. Webhooks verify raw-body signatures and deduplicate deliveries. Test transactions are RAZORPAY TEST, never live revenue. Browser callbacks cannot independently mark an order paid. Cancelled checkout leaves an unpaid order; Retry checkout reuses its intent. If payment is awaiting capture or the callback failed, use Check payment status: the backend fetches the provider order's payments and releases at most one job after capture is confirmed. This check does not resolve an ambiguous order-creation timeout without a provider order ID.

Owners can request full refunds under Payments. Requests and webhook outcomes are recorded separately. Refunds do not undo printing or retry jobs. Ambiguous provider timeouts leave RECONCILIATION_REQUIRED intents to prevent duplicate charges; operators must reconcile them with Razorpay before further action. Automated reconciliation for these timeouts is not implemented. A capture arriving after document expiry is flagged for refund review, not printed.

[Official Razorpay integration guide](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/).

## Database And Email

Local database: `printsetu.db` (SQLite). Tables include users, shops, pricing, documents, orders, devices/printers, jobs, payments/refunds, sessions, OTP challenges, webhook deduplication, rate limits and audit logs. Alembic upgrades existing data without resetting it. Back up before migrations.

For Neon, create PostgreSQL and set its connection details:

```dotenv
PRINTSETU_DATABASE_URL=postgresql+psycopg://USER:URL_ENCODED_PASSWORD@NEON_HOST/DB?sslmode=require
```

Run `python -m alembic upgrade head`. This initializes the selected database; it does not transfer existing SQLite records. Configure backups and database access restrictions in your account. Keep secrets out of source control.

For real OTP email, verify a domain in Resend and configure:

```dotenv
PRINTSETU_OTP_MODE=resend
PRINTSETU_RESEND_API_KEY=re_YOUR_KEY
PRINTSETU_RESEND_SENDER=PrintSetu <verify@your-domain.com>
```

OTPs expire in ten minutes with five attempts and resend cooldown. Codes are hashed; passwords use Argon2. Sessions use HttpOnly cookies and CSRF tokens; owner routes enforce tenant ownership, admin routes enforce roles. Development OTP disclosure is disabled in Resend mode. [Resend API](https://resend.com/docs/api-reference/emails/send-email).

## Production

Use `.env.production.example` with your own secrets. Production startup requires HTTPS, PostgreSQL, Resend, live Razorpay and payment enforcement; demo and unpaid printing must be off. Validate sandbox on isolated staging (`PRINTSETU_ENV=staging`, payment required, unpaid disabled), not live production.

Run `npm run build` in `frontend`. Serve `frontend/dist` with HTTPS and SPA fallback; forward `/api` to FastAPI, preserving Host and trusted scheme. Disable buffering and allow long SSE connections. Set the exact public origin in `PRINTSETU_ALLOWED_ORIGINS`. An adaptable proxy template is in `deploy/nginx.conf`.

```powershell
cd D:\qrprint
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m backend.app.bootstrap_admin
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

Admin bootstrap is interactive and operator-only, not a signup role selector. Trust forwarded headers only from your actual proxy. Configure a service manager, private persistent storage, monitoring, backups and least-privilege database access. Scaled deployments need shared private document storage. Do not publicly serve the storage directory.

Uploads are limited to 25 MB; malformed, encrypted, empty and excessive-page PDFs are rejected. Documents/previews are customer-private; agents access only assigned jobs. Cleanup runs every 60 seconds, removes expired files, skips active rendering and records deletion errors. Retention defaults to six hours and is shop-configurable. Agent downloads use temporary directories. OS/printer spoolers have separate retention policies; app cleanup cannot guarantee their deletion. Crashes may leave OS temporary directories for machine maintenance.

## Build And Verify

```powershell
cd D:\qrprint
.\.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
npm run build
npx playwright install chromium
npm run test:e2e
cd ..
.\'Build Shop Connector.ps1'
```

Browser tests require both servers in local unpaid demo mode. Native output: `dist\PrintSetu-Connector.exe`. With Inno Setup, also `dist\PrintSetu-Setup.exe`, a per-user installer requiring no administrator rights. Inno Setup 6.7 requires appropriate licensing for commercial use; obtain it before commercial distribution. Release binaries also need an organization-controlled signing certificate to avoid unsigned-publisher warnings.

Verified here: backend tests, desktop/mobile browser flow, printer discovery/driver context, packaged app startup and installer compilation. Not verified: your Neon account, actual Resend delivery, actual Razorpay sandbox checkout/webhooks/refunds, physical output, clean-PC installer installation/uninstallation or public production deployment. Provide external credentials and complete staging validation before accepting paid jobs.
