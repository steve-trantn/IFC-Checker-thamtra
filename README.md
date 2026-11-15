# Thẩm Tra Mô Hình BIM — kiểm tra thông tin phi hình học IFC theo BEP

Phần mềm (desktop Windows **và** web) kiểm tra tự động hàng loạt file IFC xem đối tượng có **đủ thông tin phi hình học**, **đúng yêu cầu BEP/EIR (LOIN)** và **đúng chủng loại IFC** hay không. Có thể dùng AI (Claude) để hỗ trợ nếu người dùng có API key; không có AI phần mềm vẫn chạy đầy đủ.

**Người phát triển:** Steve Tran — ĐT: 0987 779 228

## Cài đặt & chạy

```bat
pip install -r requirements.txt
run.bat                      :: bản desktop  (hoặc: python main.py)
run_web.bat                  :: bản web, mở trong trình duyệt (hoặc: streamlit run web_app.py)
```

Đóng gói thành `.exe` để chạy trên máy không cài Python: chạy `build_exe.bat` → `dist\ThamTraBIM\ThamTraBIM.exe`.

## Đưa bản web lên mạng

- **Máy chủ nội bộ công ty (khuyến nghị cho hồ sơ dự án):** trên 1 máy chạy `streamlit run web_app.py --server.address 0.0.0.0 --server.port 8501`, đồng nghiệp truy cập `http://<IP-máy>:8501`. File IFC không ra khỏi mạng công ty.
- **Streamlit Community Cloud (miễn phí):** đẩy mã nguồn lên GitHub → vào share.streamlit.io → *New app* → chọn repo, file `web_app.py`. Lưu ý: file IFC tải lên sẽ được xử lý trên máy chủ cloud — cân nhắc quy định bảo mật dự án. API key AI đặt trong *Secrets* với tên `ANTHROPIC_API_KEY`.

## Quy trình

1. **Hồ sơ IFC** – thêm file/thư mục (IFC2X3, IFC4, IFC4X3; nhiều bộ môn cùng lúc).
2. **Quy tắc BEP** – mở bộ quy tắc Excel (`templates/BEP_Rules_Mau.xlsx`), sửa trong Excel rồi bấm *Tải lại*; hoặc **Nhập Phụ lục LOI từ BEP** (.docx/.xlsx có bảng *Nhóm | Thông tin | Kiểu*) — phần mềm tự đọc bảng, tự gán hạng mục với file IFC theo tên file, không cần AI; hoặc dùng AI đọc BEP dạng khác; có thể chạy kèm file **IDS** (chuẩn buildingSMART).
3. **Chạy kiểm tra** → tab *Kết quả*: tỷ lệ đạt theo file / theo quy tắc / chi tiết từng đối tượng. Nhấp đúp để xem toàn bộ thuộc tính; chuột phải để copy GlobalId (dán vào Navisworks/Revit/BIMcollab để tìm).
4. **Xung đột & 3D** – kiểm tra va chạm giữa các mô hình, xem mô hình 3D bên cạnh bảng lỗi/xung đột (xem mục dưới).
5. **Thống kê** – số đối tượng theo lớp IFC; mức độ điền của từng thuộc tính (đối chiếu nhanh với BEP).
6. **Xuất báo cáo** Excel (biểu đồ, xung đột) kèm HTML; xung đột xuất thêm **BCF 2.1**.

## Xem mô hình 3D & kiểm tra xung đột

- **Xem 3D**: bản web hiển thị khung 3D ngay **bên cạnh** bảng lỗi / bảng xung đột; bản desktop mở cửa sổ 3D trong trình duyệt và **liên kết 2 chiều** với phần mềm (chọn dòng → camera phóng tới đối tượng; nháy chọn trong 3D → chọn dòng lỗi). Tô màu theo kết quả kiểm tra (đỏ lỗi / vàng cảnh báo / xanh đạt), theo file hoặc theo lớp IFC; đối tượng bị che khuất (vd hố ga dưới mặt đường) hiện dạng x-quang. Thư viện three.js đóng gói sẵn — chạy được trong mạng nội bộ không có Internet.
- **Xung đột**: mỗi phép thử so nhóm A với nhóm B (lọc theo **tên file** + **lớp IFC**), mặc định tạo theo từng cặp file. Va chạm tính trên lưới tam giác bằng thư viện FCL, báo **độ sâu xuyên** để lọc theo dung sai. Nên tắt các cặp tiếp xúc theo thiết kế (vd hố ga × mặt đường) theo ma trận va chạm trong BEP. Với bề mặt hở (địa hình, mặt đường) độ sâu chỉ mang tính tham khảo.
- Phần mềm tự xử lý lỗi xuất IFC hay gặp: `IfcStyledItem` bị đặt nhầm vào danh sách hình học (làm nhiều phần mềm không hiển thị được đối tượng) — vẫn dựng hình được và báo lỗi này trong trạng thái file.

## Bộ quy tắc (sheet `Rules`)

| Cột | Ý nghĩa |
|---|---|
| Lớp IFC áp dụng | `IfcWall;IfcSlab` (gồm lớp con), `*` = mọi đối tượng, `!IfcX` để loại trừ |
| Lọc theo tên file | Regex trên tên file IFC, vd `THOATNUOC` hoặc `CONGNGANG` — áp quy tắc theo hạng mục khi tên file theo quy tắc đặt tên của BEP |
| Đối tượng KT | `PROPERTY`, `ATTRIBUTE`, `CLASS`, `CONTAINER`, `MATERIAL`, `CLASSIFICATION` |
| Property Set / Tên thuộc tính | So khớp **không phân biệt hoa/thường, dấu tiếng Việt, khoảng trắng thừa**; `*` = mọi Pset; hỗ trợ `Pset_*Common` |
| Điều kiện | `EXISTS`, `EQUALS`, `NOT_EQUALS`, `IN`, `NOT_IN`, `REGEX`, `RANGE` (`0..100`), `NUMBER` |
| Mức độ | `ERROR` (bắt buộc – tính vào tỷ lệ đạt), `WARNING`, `INFO` |

Sheet `ClassMapping`: từ khoá trong tên (không dấu, regex) → lớp IFC đúng. Dùng để phát hiện xuất sai chủng loại (vd tên có “hố ga” nhưng là `IfcBuildingElementProxy`).

Số giá trị kiểu `136.35m3` được đọc thành số; giá trị `' '`, `-`, `N/A`... coi là trống (sửa trong Cài đặt).

## Trợ lý AI (tuỳ chọn)

Nhập API key Claude trong **Cài đặt** (hoặc biến môi trường `ANTHROPIC_API_KEY`). Model mặc định `claude-opus-5-5`; có thể chọn `claude-sonnet-5-5` / `claude-haiku-5-5` cho rẻ hơn.

- **Đề xuất lớp IFC đúng** cho các nhóm đối tượng Proxy / nghi sai chủng loại (gộp theo tên loại để tiết kiệm).
- **Tạo quy tắc từ file BEP** (PDF/DOCX/XLSX/TXT) → rà soát → lưu Excel.
- **Viết nhận xét thẩm tra** (đưa vào báo cáo) và **hỏi đáp** về kết quả.

Chỉ gửi dữ liệu tóm tắt (tên lớp, tên loại, tên/giá trị mẫu thuộc tính, thống kê) — **không gửi file IFC/hình học**. Lần đầu dùng phần mềm sẽ hỏi xác nhận.

## Dòng lệnh (chạy hàng loạt / tự động)

```bat
python main.py check "D:\HoSo\*.ifc" --rules BEP_Rules.xlsx --ids yeucau.ids --out BaoCao.xlsx --html BaoCao.html
python main.py check D:\HoSo --fail-under 90      :: trả mã lỗi 1 nếu tỷ lệ đạt < 90%
python main.py template BEP_Rules.xlsx
```

## Cấu trúc mã

```
main.py                    điểm khởi chạy (GUI / CLI)
ifc_checker/model_loader   đọc IFC, trích thuộc tính, vật liệu, phân loại, không gian chứa, assembly
ifc_checker/rules          bộ quy tắc, đọc/ghi Excel-JSON, bộ mẫu mặc định
ifc_checker/engine         áp quy tắc, kiểm tra tên ↔ lớp IFC, thống kê
ifc_checker/ids_runner     chạy IDS bằng ifctester
ifc_checker/bep_import     nhập Phụ lục LOI từ BEP (.docx/.xlsx) thành quy tắc
ifc_checker/report         xuất Excel / HTML / JSON
ifc_checker/geometry       dựng lưới tam giác (ifcopenshell.geom), dùng chung cho 3D và xung đột
ifc_checker/clash          kiểm tra xung đột (FCL)
ifc_checker/bcf            xuất BCF 2.1
ifc_checker/viewer/        trình xem 3D (three.js) + trang cho web / desktop
ifc_checker/viewer_data    đóng gói hình học + trạng thái cho trình xem
ifc_checker/viewer_server  máy chủ cục bộ cho trình xem của bản desktop
ifc_checker/web_viewer     nhúng trình xem vào Streamlit
ifc_checker/ai             tích hợp Claude (tuỳ chọn)
ifc_checker/gui            giao diện desktop (tkinter)
web_app.py                 giao diện web (Streamlit)
```

## Lưu ý

- “Đối tượng đạt” = không vi phạm quy tắc mức ERROR.
- Cụm đối tượng (cha - con), chế độ *Tự động*: cha chỉ là nhóm (không có thuộc tính) thì kiểm tra từng con; cha mang thông tin thì con là khối hình học tách nhỏ và được bỏ qua. Đổi trong Cài đặt.
- Kết quả AI chỉ mang tính hỗ trợ; kết luận thẩm tra do chuyên gia quyết định.
