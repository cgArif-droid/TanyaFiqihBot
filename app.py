

def acquire_telegram_process_lock() -> bool:
    """Cuba kunci proses pada Linux supaya worker lain tidak memulakan polling kedua."""
    global _TELEGRAM_LOCK_HANDLE

    try:
        import fcntl
    except ImportError:
        print(
            "[TELEGRAM] Kunci antara proses tidak tersedia; "
            "pastikan hanya satu worker."
        )
        return True

    lock_path = os.getenv(
        "TELEGRAM_LOCK_FILE",
        "/tmp/tanyafiqihbot_telegram.lock",
    )

    try:
        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            print(
                "[TELEGRAM] Instance lain sudah memegang kunci polling; "
                "polling kedua dibatalkan."
            )
            return False

        _TELEGRAM_LOCK_HANDLE = handle
        return True

    except Exception as exc:
        print(f"[TELEGRAM LOCK WARNING] Tidak dapat mendapatkan kunci proses: {exc}")
        return True


def start_telegram_background():
    """Mulakan satu thread Telegram bagi proses Python ini sahaja."""
    global _TELEGRAM_THREAD, _TELEGRAM_STATUS

    if not TELEGRAM_AUTOSTART:
        _TELEGRAM_STATUS = "disabled"
        print("[TELEGRAM] Autostart dimatikan melalui TELEGRAM_AUTOSTART.")
        return None

    if not TELEGRAM_TOKEN:
        _TELEGRAM_STATUS = "not_configured"
        print("[WARNING] TELEGRAM_TOKEN tiada. Telegram polling tidak dimulakan.")
        return None

    debug_enabled = os.getenv("FLASK_DEBUG", "").strip().lower() in {"1", "true", "yes"}
    if debug_enabled and os.getenv("WERKZEUG_RUN_MAIN", "").lower() != "true":
        _TELEGRAM_STATUS = "waiting_for_reloader"
        print("[TELEGRAM] Menunggu proses Flask reloader sebenar.")
        return None

    with _TELEGRAM_THREAD_LOCK:
        if _TELEGRAM_THREAD is not None and _TELEGRAM_THREAD.is_alive():
            print("[TELEGRAM] Thread polling sudah berjalan; tidak memulakan thread kedua.")
            return _TELEGRAM_THREAD

        if not acquire_telegram_process_lock():
            _TELEGRAM_STATUS = "duplicate_instance"
            return None

        _TELEGRAM_THREAD = threading.Thread(
            target=start_telegram,
            name="telegram-supervisor",
            daemon=True,
        )
        _TELEGRAM_THREAD.start()
        return _TELEGRAM_THREAD


# ============================================================
# FLASK HEALTH ENDPOINTS
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "service": "TanyaFiqihBot",
        "status": "running",
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "TanyaFiqihBot",
        "gemini_configured": bool(GOOGLE_API_KEY),
        "telegram_configured": bool(TELEGRAM_TOKEN),
        "telegram_autostart": TELEGRAM_AUTOSTART,
        "telegram_status": _TELEGRAM_STATUS,
        "telegram_thread_alive": bool(
            _TELEGRAM_THREAD is not None and _TELEGRAM_THREAD.is_alive()
        ),
        "telegram_last_error": _TELEGRAM_LAST_ERROR,
        "brave_configured": bool(BRAVE_SEARCH_API_KEY),
        "turath_service_url": TURATH_SERVICE_URL,
        "website_search_options": {
            key: {"name": name, "domain": domain}
            for key, (name, domain) in WEBSITE_SEARCH_OPTIONS.items()
        },
    })


# ============================================================
# START BACKGROUND TELEGRAM SERVICE
# ============================================================

# Penting: jika menggunakan Gunicorn, guna --workers 1 untuk fail gabungan ini.
# Untuk deployment berasingan, set TELEGRAM_AUTOSTART=0 pada servis web.
start_telegram_background()
