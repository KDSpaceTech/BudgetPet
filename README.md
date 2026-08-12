# BudgetPet — Python + Turso Cloud Multi-User Demo

BudgetPet là ứng dụng quản lý chi tiêu theo hướng **Python-centered**, có AI OCR hóa đơn và gamification nuôi Pet.

## Kiến trúc hiện tại

- **Python**: business logic, API, authentication, money flow, 6 Hũ, goals, recurring bills, Pet.
- **HTML/CSS/JavaScript**: giao diện mobile và gọi API Python.
- **Gemini API**: OCR và hỗ trợ phân loại bill.
- **Turso Cloud**: lưu dữ liệu online bằng SQLite-compatible database.
- **Render Free**: chạy Python web service.

### Multi-user isolation

Để giữ code hiện tại dễ sửa, BudgetPet dùng mô hình **1 database Turso cho auth + 1 database Turso riêng cho mỗi user**. Turso Platform API hỗ trợ trực tiếp kiến trúc database-per-user; gói Free hiện quảng cáo 100 databases, 5 GB storage, 500M rows read/tháng và 10M rows written/tháng, nên phù hợp demo tối đa 30 user nếu nằm trong quota. https://turso.tech/pricing

Mỗi user có database riêng:

```text
Turso
├── budgetpet-auth
├── budgetpet-user-xxxx
├── budgetpet-user-yyyy
└── ...
```

Dữ liệu User A không truy vấn cùng database với User B.

## 1. Tạo Turso

Turso hiện cung cấp CLI cho Windows qua WSL và Platform API để quản lý database/token. Tài liệu Python hiện hỗ trợ remote access; BudgetPet dùng SQL-over-HTTP bằng Python standard library để tránh thêm SDK native, trong khi vẫn sử dụng `/v2/pipeline` của Turso. https://docs.turso.tech/sdk/python/quickstart

### Bước A — đăng nhập Turso

Theo tài liệu Turso:

```powershell
turso auth signup
# hoặc
turso auth login
```

Lấy account/org slug:

```powershell
turso org list
```

Tạo Platform API token:

```powershell
turso auth api-tokens mint budgetpet
```

Tài liệu Platform API của Turso dùng token này để tạo database và database token. https://docs.turso.tech/api-reference/quickstart

### Bước B — chuẩn bị group

Xem group:

```powershell
turso group list
```

Nếu account của bạn chưa có group phù hợp, tạo group:

```powershell
turso group create default
```

Free plan chỉ cho phép một group; dùng group hiện có là đủ cho demo. https://docs.turso.tech/cli/group/create

### Bước C — tạo database auth

Có thể tạo bằng CLI:

```powershell
turso db create budgetpet-auth --group default
```

Lấy HTTP URL:

```powershell
turso db show budgetpet-auth --http-url
```

Tạo auth token:

```powershell
turso db tokens create budgetpet-auth --expiration 30d
```

Turso hiện tài liệu hóa các bước lấy HTTP URL và database auth token cho remote access. https://docs.turso.tech/sdk/http/quickstart

## 2. Cấu hình Render

Trong Render → **Environment**, thêm:

```text
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-flash-latest
GEMINI_FALLBACK_MODELS=gemini-3.1-flash-lite

TURSO_DATABASE_URL=https://...turso.io
TURSO_AUTH_TOKEN=...
TURSO_ORG=...
TURSO_GROUP=default
TURSO_PLATFORM_TOKEN=...
TURSO_TOKEN_EXPIRATION=30d
```

Không commit các token này vào GitHub.

`TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` là credentials của **auth database**.
`TURSO_ORG` + `TURSO_PLATFORM_TOKEN` dùng để BudgetPet tự provision database riêng cho tài khoản mới.

Turso Platform API hỗ trợ create database và create scoped database token cho mô hình database-per-user. https://docs.turso.tech/api-reference/databases/create https://docs.turso.tech/api-reference/databases/create-token

## 3. Local development

Nếu để toàn bộ biến TURSO trống, app tự động chạy local SQLite như trước:

```powershell
python app.py
```

Mở:

```text
http://localhost:3000
```

Điều này giúp sửa code dễ dàng mà không bắt buộc phải kết nối Internet/Turso khi phát triển giao diện.

## 4. Khi deploy Turso

Sau khi push GitHub:

```powershell
python -m py_compile app.py
git add .
git commit -m "Switch BudgetPet storage to Turso"
git push origin main
```

Render sẽ build lại nếu Auto Deploy đang bật.

## 5. Luồng lưu dữ liệu

```text
Phone / PC
    ↓
Render Free
    ↓
Python BudgetPet
    ├── Auth DB → budgetpet-auth
    └── User DB → budgetpet-user-xxxx
```

Render có thể sleep, nhưng dữ liệu nằm trên Turso Cloud thay vì filesystem của Render.

## 6. Money flow

- Thu nhập → Income Router → 6 Hũ.
- Tổng 6 Hũ phải đúng 100%; phần dư làm tròn dồn vào Hũ Thiết yếu.
- Bill → OCR → phân loại → chọn Hũ → trừ tiền Hũ.
- Hũ âm được phép và kích hoạt Red Flag.
- Hóa đơn định kỳ tự động tạo giao dịch tối đa một lần mỗi hóa đơn/tháng.
- Mục tiêu lấy tiền từ Hũ Tiết kiệm.

## 7. Sửa code

Turbso layer nằm riêng trong:

```text
turso_http.py
```

Vì vậy phần business logic trong `app.py` vẫn gần như giữ nguyên. Khi cần sửa UI/logic, chỉ cần sửa `app.py` hoặc `templates/`.

Kiểm tra:

```powershell
python -m py_compile app.py
```

## 8. Lưu ý về quota

Turso Free hiện quảng cáo:

- 100 databases
- 5 GB storage
- 500M rows read/month
- 10M rows written/month

Đây là quota của dịch vụ và có thể thay đổi; theo dõi Usage trước khi mở rộng demo. https://turso.tech/pricing
