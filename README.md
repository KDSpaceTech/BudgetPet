# BudgetPet — Multi-User Demo (tối đa 30 tài khoản)

BudgetPet là ứng dụng quản lý chi tiêu thuần Python với AI OCR hóa đơn và gamification nuôi Pet.

## Điểm mới của bản Multi-User

- Giữ nguyên giao diện BudgetPet mobile hiện tại.
- Đăng ký tài khoản.
- Đăng nhập / đăng xuất.
- Mỗi tài khoản có **SQLite database riêng** trong `database/users/`.
- Dữ liệu của User A không dùng chung với User B.
- Tách riêng Pet, tên người dùng, avatar, giao dịch, 6 Hũ, mục tiêu và hóa đơn định kỳ.
- Giới hạn demo: **30 tài khoản**.
- Mật khẩu được hash bằng PBKDF2-HMAC-SHA256, không lưu plain text.
- Session dùng cookie HttpOnly.
- Gemini API key chỉ đọc từ biến môi trường `GEMINI_API_KEY`.

## Chạy local

```powershell
python app.py
```

Mở:

```text
http://localhost:3000
```

Điện thoại cùng Wi-Fi có thể dùng IP LAN của máy tính:

```text
http://IP-MAY-TINH:3000
```

## Gemini API key

Không đặt key thật trong source code. Thiết lập biến môi trường:

PowerShell:

```powershell
$env:GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
python app.py
```

Khi deploy Render/Docker, đặt `GEMINI_API_KEY` trong Environment Variables/Secrets.

## GitHub

Không commit:

- `.env`
- `database/*.db`
- `database/users/`

Các file này đã được chặn bởi `.gitignore`.

## Demo multi-user

Tài khoản được lưu trong `database/auth.db`. Mỗi user có một database riêng, ví dụ:

```text
database/
├── auth.db
└── users/
    ├── <user-db-1>.db
    ├── <user-db-2>.db
    └── ...
```

Do đó thay đổi ở tài khoản này không làm thay đổi dữ liệu của tài khoản khác.
