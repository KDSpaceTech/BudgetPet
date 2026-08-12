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

## Money-flow automation (v2)

- Monthly income is routed into six jars by percentage. The six percentages must total exactly 100%; rounding leftovers are assigned to **Nhu cầu thiết yếu**.
- OCR flow is **read → classify → route to jar → record expense**. Hobby/personal-shopping keywords such as keycap/keyboard are routed to **Hưởng thụ**.
- Jar balances are allowed to go negative. A negative jar is marked as a red alert and the Pet displays a warning message.
- Recurring bills support a due day and target jar. A background worker checks bills automatically and creates at most one payment transaction per bill/month.
- Goal deposits are transfers out of **Tiết kiệm**. Deposits are rejected when the savings jar does not have enough balance.
- Gemini OCR uses retry/backoff for transient 429/5xx errors and supports a fallback model. Configure `GEMINI_MODEL` and `GEMINI_FALLBACK_MODELS` through environment variables.


## Persistent SQLite on Render (Cách A)

Để giữ dữ liệu tài khoản qua restart/redeploy, tạo Persistent Disk trên Render và mount vào:

```text
/data
```

Container đã đặt `DATA_DIR=/data`, nên BudgetPet lưu:

```text
/data/auth.db
/data/users/*.db
```

SQLite được bật WAL + busy timeout để chịu nhiều request đồng thời tốt hơn.

### Render
1. Web Service → Disks → Add Disk.
2. Mount Path: `/data`.
3. Deploy lại.
4. Không đặt database vào GitHub.

### Local
Không cần `/data`; mặc định `DATA_DIR=./database`.

### Backup
Persistent Disk không thay thế backup. Có thể sao lưu thư mục `/data` định kỳ.


## Lưu dữ liệu bền vững trên Render

Render mặc định dùng filesystem tạm. Với cách A (SQLite + Persistent Disk), Web Service cần một Persistent Disk và ứng dụng phải ghi dữ liệu dưới mount path. Render hiện yêu cầu Persistent Disk cho Web Service trên gói trả phí; disk được giữ qua restart/redeploy, nhưng chỉ một instance có thể dùng disk và service không scale ngang khi disk được gắn. Xem:
https://render.com/docs/disks

Khuyến nghị:
- Mount path: `/data`
- Environment: `DATA_DIR=/data`
- Không commit database lên GitHub.
- Có thể chạy `python backup_database.py` để tạo backup SQLite an toàn trong `DATA_DIR/backups/`.

## Quy trình sửa code

Sửa trực tiếp `app.py` hoặc `templates/index.html`, sau đó:

```powershell
python -m py_compile app.py
git add .
git commit -m "Describe your change"
git push origin main
```

Render sẽ tự deploy khi Auto-Deploy/On Commit được bật.
