# Thẩm Tra Mô Hình BIM

Ứng dụng web thẩm tra hồ sơ mô hình BIM định dạng IFC: kiểm tra **thông tin phi hình học theo BEP/LOIN**, **chủng loại IFC**, **xung đột theo ma trận va chạm**, xem **mô hình 3D** và **hỏi đáp mô hình bằng tiếng Việt** với trợ lý AI (tuỳ chọn).

**Người phát triển:** Steve Tran — ĐT: 0987 779 228

## Chạy thử

```bat
pip install -r requirements.txt
run_web.bat            :: mở http://localhost:8000
```

Đồng nghiệp cùng mạng LAN truy cập `http://<IP-máy-chạy>:8000`. Mọi xử lý diễn ra trên máy chủ này, file IFC không ra ngoài.

## Giao diện

| Khu vực | Nội dung |
|---|---|
| Thanh công cụ | Mở IFC · Sức khỏe mô hình · Quy tắc BEP · ▶ Chạy kiểm tra · Ma trận va chạm · Chạy xung đột · Tô màu kết quả · Cô lập đối tượng lỗi · Hỏi mô hình · Báo cáo Excel/HTML |
| Giữa | Mô hình 3D (tô màu đỏ lỗi / vàng cảnh báo / xanh đạt, x-quang đối tượng bị che, điểm xung đột) |
| Trái | **Thuộc tính** đối tượng đang chọn (thuộc tính gốc, Pset, lỗi, xung đột) · **Trình duyệt dự án** (file → lớp IFC: tô sáng / cô lập) |
| Dưới | Sức khỏe mô hình · Quy tắc BEP (sửa trực tiếp) · Kết quả · Xung đột · Thống kê thuộc tính · Danh sách AI · Nhật ký |
| Phải | **Hỏi mô hình**: trợ lý AI dùng công cụ của phần mềm để truy vấn, đếm trực tiếp trên mô hình rồi trả lời; mỗi kết quả có nút *Danh sách / Highlight 3D / Cô lập* |

Bấm 1 dòng lỗi hoặc xung đột → mô hình bay tới đối tượng; nháy chọn trong 3D → hiện thuộc tính.

## Kiểm tra thông tin theo BEP

- Bộ quy tắc: Excel (`templates/BEP_Rules_Mau.xlsx`), JSON, hoặc file **IDS** (buildingSMART).
- **Nhập Phụ lục LOI từ BEP** (.docx/.xlsx có bảng *Nhóm | Thông tin | Kiểu*): tự tạo quy tắc, tự gán hạng mục với file theo quy tắc đặt tên file — không cần AI.
- So khớp tên Pset/thuộc tính không phân biệt hoa/thường, dấu tiếng Việt, khoảng trắng thừa; tự nhận biết cụm cha–con (nhóm thuần vs khối hình học tách nhỏ).
- Phát hiện lỗi xuất IFC hay gặp (`IfcStyledItem` lẫn trong hình học, tên mất ký tự tiếng Việt, đối tượng phẳng tại cao độ 0).

## Ma trận va chạm

4 loại kiểm tra — định nghĩa "thế nào là va chạm" cho từng cặp nhóm:

| Loại | Va chạm khi | Ví dụ trong ma trận mẫu |
|---|---|---|
| Cao độ khớp mặt (FLUSH) | Đỉnh A lệch bề mặt B quá ± giá trị | Mặt hố ga × mặt đường / vỉa hè: ±20 mm (hố ga nằm trong kết cấu đường là đúng thiết kế) |
| Chiều sâu chôn (COVER) | Từ bề mặt B xuống đỉnh A nhỏ hơn giá trị | Ống thoát nước dưới lòng đường ≥ 0,7 m, dưới vỉa hè ≥ 0,5 m |
| Khoảng cách tối thiểu (CLEARANCE) | Gần hơn giá trị (kể cả giao nhau) | Ống TNM × ống TNT 0,2 m; cây × ống 1,0 m; cột đèn × ống 0,5 m |
| Va chạm cứng (HARD) | Hai khối giao nhau | Ống TNM xuyên hố ga TNT; biển báo trong phần xe chạy |

Ma trận mẫu `templates/Ma_tran_va_cham_mau.xlsx` (24 phép thử, nhóm A1…D2 theo BEP hạ tầng). Nhóm nhận diện theo từ khoá tên (kể cả tên bị mất dấu khi xuất IFC), lớp IFC, tên file. **Các giá trị là giá trị mẫu** — đối chiếu QCVN 07:2023/BXD, TCVN 7957 và hồ sơ thiết kế. Kết quả xuất Excel và **BCF 2.1** (Navisworks, BIMcollab, Solibri).

## Trợ lý AI (tuỳ chọn)

Nhập API key Claude trong *Cài đặt* (chỉ giữ trong bộ nhớ phiên) hoặc đặt biến môi trường `ANTHROPIC_API_KEY` trên máy chủ. AI chỉ nhận kết quả truy vấn tóm tắt (tên lớp, tên, thuộc tính, thống kê), không nhận file IFC. Kết luận thẩm tra do chuyên gia quyết định.

## Deploy

- **Máy chủ công ty (khuyến nghị cho hồ sơ dự án):** chạy `run_web.bat` trên 1 máy Windows, hoặc Docker: `docker build -t thamtra . && docker run -p 8000:7860 thamtra`.
- **Hugging Face Spaces (miễn phí, 16 GB RAM):** tạo Space loại *Docker*, đẩy mã nguồn này lên (Dockerfile có sẵn, cổng 7860). Thêm `ANTHROPIC_API_KEY` trong *Settings → Secrets* nếu dùng AI.
- **Render / Railway / VPS:** dùng Dockerfile. Lưu ý gói miễn phí thường chỉ 512 MB RAM — không đủ cho mô hình lớn.

Khi đưa lên Internet: file IFC tải lên được xử lý trên máy chủ đó — cân nhắc quy định bảo mật dự án.

## Cấu trúc mã

```
webapp/server.py           máy chủ web (FastAPI) - API cho giao diện
webapp/assistant.py        trợ lý AI "Hỏi mô hình" (công cụ truy vấn mô hình)
webapp/static/             giao diện (HTML/CSS/JS, không cần build)
ifc_checker/model_loader   đọc IFC, thuộc tính, vật liệu, phân loại, không gian, cụm cha-con
ifc_checker/rules          bộ quy tắc BEP (Excel/JSON) · bep_import: nhập Phụ lục LOI
ifc_checker/engine         áp quy tắc, kiểm tra tên ↔ lớp IFC, thống kê · ids_runner: IDS
ifc_checker/clash          ma trận va chạm 4 loại (FCL + chiếu đứng) · bcf: xuất BCF
ifc_checker/geometry       dựng lưới tam giác · viewer_data: đóng gói cảnh 3D
ifc_checker/viewer/        trình xem 3D (three.js đóng gói sẵn, chạy offline)
ifc_checker/report         báo cáo Excel / HTML
main.py, ifc_checker/gui   bản desktop (tạm dừng phát triển)
web_app.py                 bản web Streamlit cũ (thay bằng webapp/)
```

Dòng lệnh: `python main.py check "D:\HoSo\*.ifc" --rules BEP_Rules.xlsx --out BaoCao.xlsx`
