# Classic Pattern Explorer 3.3

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [Tiếng Việt](README.vi.md)

Classic Pattern Explorer là một ứng dụng máy tính và công cụ dòng lệnh giúp tìm kiếm các đoạn có thể chứa mẫu câu, chẳng hạn như 以…爲 hoặc 雖…而, trong các văn bản Hán văn (Literary Sinitic). Mỗi kết quả được hiển thị kèm ngữ cảnh từ văn bản hoặc đoạn trích do nguồn cung cấp, cùng liên kết xuất xứ (khi có) để nhà nghiên cứu có thể tự đọc và đưa ra nhận định. Giao diện hỗ trợ tiếng Việt, tiếng Hàn, tiếng Nhật, tiếng Trung giản thể, tiếng Trung phồn thể và tiếng Anh; việc thay đổi ngôn ngữ giao diện không bao giờ làm biến đổi hay thay thế văn bản gốc.

Đây là một công cụ hỗ trợ nghiên cứu nhân văn số, không phải là bộ phân tích cú pháp hay phán quyết ngữ pháp tự động. Công cụ thu thập các kết quả khả dĩ, và trong chế độ Tìm kiếm cặp, phân loại chúng theo các dấu hiệu cấu trúc thành nhóm kết quả khả năng phù hợp cao và thấp hơn; việc đọc và phán đoán ngữ nghĩa hoàn toàn thuộc về nhà nghiên cứu.

## Phạm vi tìm kiếm

Phiên bản 3.3 tổ chức các kết nối trực tuyến theo 5 khu vực với 19 nguồn trực tuyến, bên cạnh các thư mục văn bản cục bộ trên máy tính. Các nguồn trực tuyến trả về đoạn trích hoặc bản ghi thư mục giới hạn theo hạn mức của từng nguồn.

| | Vị trí văn bản | Kết quả nhận được |
|---|---|---|
| **Văn bản của bạn** | Thư mục trên máy tính của bạn: các tệp UTF-8 `.txt` và `.md`, các tác phẩm nhập từ Chinese Text Project (CTP/CText, ctext.org) bằng khóa API của bạn (lưu thành `.txt`), và các gói `.texts.jsonl` do chương trình chuyển đổi từ tài liệu gốc của các viện nghiên cứu thuộc Cổng dữ liệu công cộng Hàn Quốc (Viện Lịch sử Quốc gia Hàn Quốc, Viện Dịch thuật Kinh điển Hàn Quốc, Học viện Nghiên cứu Hàn Quốc, v.v.) | Toàn văn mỗi tệp đều được tìm kiếm; phạm vi bao quát toàn bộ nội dung tệp |
| **Tìm kiếm đoạn trích và thư mục trực tuyến** | 19 nguồn trực tuyến thuộc 5 khu vực (Hàn Quốc 8, Nhật Bản 5, Trung Quốc 1, Đài Loan 4, Việt Nam 1); yêu cầu kết nối Internet | Các đoạn văn hoặc bản ghi thư mục do nguồn trả về quanh vị trí khớp (thường bị cắt ở hai đầu), trong giới hạn số lượng của từng nguồn |

Kết quả trực tuyến là các đoạn văn hoặc bản ghi thư mục do máy chủ nhà cung cấp trả về theo giới hạn truy vấn. Việc không tìm thấy kết quả không chứng minh rằng mẫu câu đó không bao giờ xuất hiện trong kho tư liệu lịch sử. Trước khi trích dẫn, hãy luôn mở liên kết xuất xứ để kiểm tra toàn bộ ngữ cảnh.

## Bắt đầu sử dụng

### Windows (Tệp thực thi)

1. Truy cập [Trang phát hành v3.3](https://github.com/cobari/CPE-release/releases/tag/v3.3) và tải xuống tệp thực thi Windows x64 [cpe-gui.exe](https://github.com/cobari/CPE-release/releases/download/v3.3/cpe-gui.exe).
2. Nhấp đúp vào `cpe-gui.exe` để mở cửa sổ tìm kiếm. Không cần cài đặt Python hay uv.
3. Tệp thực thi sử dụng [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). Nếu ứng dụng không khởi động, hãy cài đặt WebView2 Runtime từ liên kết trên.

> **Lưu ý**: Bản tải về `cpe-gui.exe` độc lập chỉ chứa cửa sổ tìm kiếm đồ họa. Tệp không bao gồm công cụ dòng lệnh hoặc các tệp mẫu câu. Nếu bạn cần giao diện dòng lệnh (CLI), vui lòng sử dụng gói phân phối đầy đủ (mã nguồn) dưới đây.

### macOS và Linux (Trình khởi chạy gói phân phối)

Tệp thực thi Windows không chạy được trên macOS hoặc Linux. Vui lòng sử dụng gói phân phối đầy đủ:

1. Tải gói mã nguồn (`Source code .zip` hoặc `.tar.gz`) từ [Trang phát hành v3.3](https://github.com/cobari/CPE-release/releases/tag/v3.3) và giải nén.
2. Giữ nguyên toàn bộ cấu trúc thư mục đã giải nén và chạy tệp khởi chạy phù hợp:
   - **macOS**: Nhấp đúp vào `cpe-gui.command`. (Finder sẽ mở trong cửa sổ Terminal. Ở lần chạy đầu tiên, nếu xuất hiện cảnh báo bảo mật, hãy nhấp chuột phải vào tệp và chọn "Open".)
   - **Linux**: Chạy `cpe-gui.sh`. (Chọn "Run in terminal" trong trình quản lý tệp hoặc chạy từ shell.)
   - Nếu trình khởi chạy không chạy được từ trình quản lý tệp, hãy mở terminal trong thư mục đã giải nén và chạy `sh cpe-gui.sh`.
3. Nếu hệ thống chưa cài đặt `uv`, trình khởi chạy sẽ đề xuất tự động cài đặt qua bộ cài chính thức (nhập `y` để đồng ý).
4. Lần chạy đầu tiên sẽ tự động tải về môi trường Python và các phần phụ thuộc cần thiết (yêu cầu kết nối Internet).

---

## Tìm kiếm nhanh đầu tiên (Nguồn trực tuyến)

1. Mở **Nguồn** và đánh dấu chọn các thư mục hoặc nguồn trực tuyến cần tìm kiếm. Bạn có thể chọn tối đa 4 nguồn cùng lúc.
2. Nhấp vào biểu tượng cuốn sách và tiêu đề ứng dụng để quay lại màn hình tìm kiếm.
3. Nhập từ khóa tìm kiếm và nhấn Tìm kiếm. Các nguồn trực tuyến cần có kết nối Internet; kết quả chỉ nằm trong phạm vi nguồn cung cấp trả về.

---

## Chuẩn bị văn bản (Thư mục văn bản cục bộ)

- Trong **Nguồn**, thêm một thư mục chứa các tệp văn bản UTF-8 `.txt`, `.md` hoặc tài liệu `.texts.jsonl` đã nhập. Có 4 ô thư mục riêng biệt; việc gỡ bỏ một ô không xóa các tệp trong thư mục đó.

- Mở **Nhập**, chọn thư mục đích để lưu văn bản mới, sau đó chọn **Nhập tài liệu gốc**. Thư mục đích nhập tách biệt với các thư mục được đánh dấu để tìm kiếm. Nếu cần, chương trình sẽ hỏi thư mục đích khi bạn lưu.

- Chọn một bộ phát hành của viện nghiên cứu, CTP, Project Gutenberg, NDL, tệp XML CBETA/NIKH hoặc địa chí MPIWG. Biểu mẫu nhập địa chỉ có liên kết tới trang web của nguồn tư liệu. CTP cho phép thử tải một chương không cần khóa; danh mục toàn bộ chương của tác phẩm yêu cầu khóa API CTP của bạn, và quyền truy cập do nhà cung cấp quyết định.

- Khi có sẵn, nhấp vào **Xem trước**, kiểm tra văn bản gốc, chọn các phần cần giữ lại, ghi lại thông tin ấn bản và đánh dấu vào ô xác nhận đã xem. **Tải xuống** sẽ lưu tài liệu trực tuyến; **Nhập** sẽ đưa vào tệp đã có sẵn trên máy tính. Các đơn vị văn bản không được hỗ trợ sẽ bị loại trừ kèm lý do cụ thể. Nút đóng × trên thanh tiêu đề luôn hiển thị cố định khi cuộn nội dung.

---

## Tính năng tìm kiếm và hướng dẫn thực hành

### Chế độ tìm kiếm

- **Tìm kiếm cặp (mặc định)**: Tìm các câu mà thành tố thứ nhất xuất hiện trước thành tố thứ hai, có thể có từ ngữ xen giữa. Dùng nút ⇄ để đảo vị trí hai thành tố.
- **Tìm kiếm đơn**: Tìm các câu chứa các từ ngữ chỉ định. Hỗ trợ toán tử logic `AND`, `OR`, `NOT`, cụm từ chính xác `"cụm từ"` và giới hạn khoảng cách ký tự `NEAR/n`.
- **Mẫu DSL (Pattern DSL)**: Chỉ chạy trên thư mục văn bản cục bộ. Có thể dùng khung tạo mẫu nhiều trường trực quan trên giao diện (ô từ vựng hỗ trợ các lựa chọn phân cách bằng `|`, số chữ tối thiểu/tối đa cho từng ô trong đó bỏ trống/`0` khớp hai từ liền kề, `+ Thêm từ`, chữ loại trừ theo từng ô và bộ chọn phạm vi tìm) hoặc mở tệp định nghĩa mẫu UTF-8 `.toml` (khai báo chữ bắt buộc, lựa chọn `(A|B)`, đoạn trích có tên `{A:1..20}` hoặc `{A:1..20 not 是}`, khoảng cách không trích `{..3}`, và ranh giới `@sentence`, `@clause`, hoặc `@window:N`). Kết quả dùng tiêu đề trung tính **Kết quả khớp** và **Cần rà soát phân loại** (`classification_unreviewed` đánh dấu các mẫu ngoài dạng cặp hai thành tố đơn giản chưa được rà soát cách phân nhóm tự động, không hàm ý ít khả năng hơn) và ghi tên mẫu trên từng thẻ khi mở tệp nhiều mẫu.


### Nguồn tìm kiếm (Tối đa 4 nguồn đồng thời)

Bạn có thể chọn tối đa bốn nguồn cùng lúc từ các thư mục văn bản trên máy và 19 nguồn trực tuyến thuộc 5 khu vực. Trong **Nguồn**, mục **Tìm CSDL hoặc bản tải chính thức theo tên tác phẩm** ánh xạ các tác phẩm và tùng thư kinh điển tiêu biểu tới các CSDL trực tuyến đã kết nối, gói tải sẵn `data.go.kr` và công cụ nhập ngoài, kèm nút chọn nhanh CSDL, mở hướng dẫn phạm vi và chuyển sang quy trình tải/nhập tương ứng:

- **Thư mục văn bản**: Đọc các tệp .txt và .md (văn bản UTF-8) cùng các gói văn bản .texts.jsonl trong thư mục đã chọn và mọi thư mục con. Tệp NAME.txt.manifest.toml đặt cạnh văn bản có thể ghi tiêu đề, quy ước chấm câu và niên đại (era = "唐", year = 618, year_end = 907); nếu không có, tên tệp được dùng làm tiêu đề. Các định dạng khác, chẳng hạn Excel, không được đọc.
- **Hàn Quốc (8)**:
  - **CSDL Kinh điển Hàn Quốc (ITKC)**: Tìm trực tuyến trong ba bộ nguyên văn kinh điển của ITKC—Văn bản kinh điển (`GO_AA`), Tuyển tập văn của các tác gia Hàn Quốc (韓國文集叢刊, `MO_AA`) và Tuyển tập cổ điển Hàn Quốc (韓國古典叢刊, `KP_AA`)—từng bộ sưu tập một (tầng dịch `IT_AA` bị loại trừ). ITKC trả về trích đoạn khoảng 150 ký tự quanh vị trí khớp, bị cắt ở cả hai đầu, nên kết quả chỉ hiển thị những gì có trong trích đoạn; tiêu đề mở bản ghi trên ITKC. Mỗi bộ sưu tập được kiểm tra 100 bản ghi đầu tiên. Không lưu trữ nội dung nào.
  - **Cyber Seodang (CSDL Kinh điển phương Đông)**: Tìm trong CSDL Kinh điển phương Đông (Cyber Seodang) của Viện Văn hóa Truyền thống qua Open API. Máy chủ trả về bản ghi kèm tiêu đề, mô tả và liên kết bài viết. Kết quả hiển thị văn bản trả về cùng liên kết tới bài gốc trên Cyber Seodang; toàn văn và ngữ cảnh không được bảo đảm, và việc phân biệt nguyên văn Hán văn với bản dịch trong các trường trả về vẫn chưa được xác minh, nên bản ghi chưa xác minh được giữ làm kết quả tham khảo. Kiểm tra tối đa 50 bản ghi. Không lưu trữ nội dung nào.
  - **Tra cứu tổng hợp CSDL Lịch sử Hàn Quốc**: Tra cứu cửa sổ công khai CSDL Lịch sử Hàn Quốc của Viện Lịch sử Quốc gia Hàn Quốc theo thời kỳ và mục cơ sở dữ liệu. Gửi một từ hoặc cụm từ và chỉ đọc trang đầu, tối đa 20 dòng, của thẻ nguyên văn. Hiển thị tiêu đề, trích đoạn và liên kết của nhà cung cấp; không phân trang, không tải trang bài và không lưu trữ. Đây là kết quả tham khảo, không phải khớp mẫu trên toàn văn. Nghiên cứu cá nhân phi thương mại.
  - **Thực lục triều Joseon, tìm kiếm NIKH**: Tìm trong cửa sổ tra cứu công khai Thực lục của Viện Lịch sử Quốc gia Hàn Quốc bằng một từ hoặc cụm từ. Trang đầu của kết quả nguyên văn, tối đa 50 mục, được đọc; hiển thị tiêu đề, trích đoạn và liên kết tới trang bài. Đây là tìm kiếm để tham khảo, không phải khớp mẫu trên toàn văn; trích đoạn bị cắt và dùng thẻ nguyên văn của nhà cung cấp. Mỗi lần gửi một truy vấn; không thu thập các trang kết quả và không lưu trữ nội dung nào. Nghiên cứu cá nhân phi thương mại.
  - **Tra cứu Mạng Lịch sử Đông Bắc Á của NAHF**: Tìm trong một bộ sưu tập của thư viện nguồn công khai thuộc Quỹ Lịch sử Đông Bắc Á bằng một từ hoặc cụm từ. Trang đầu của kết quả nguyên văn, tối đa 20 dòng, được đọc; hiển thị tiêu đề, trích đoạn và liên kết của nhà cung cấp. Trang tìm kiếm chính chỉ cho số lượng, nên hãy chọn bộ sưu tập trước. Đây là tìm kiếm để tham khảo, không phải khớp mẫu trên toàn văn. Không phân trang, không tải trang bài và không lưu trữ. Nghiên cứu cá nhân phi thương mại.
  - **Thẻ gỗ NRICH, Hàn Quốc**: Tải tối đa 600 bản phiên đọc thẻ gỗ công khai. Tìm trong các bản phiên đọc đã tải (kể cả chữ Idu) để hiển thị tài liệu có ký tự khớp với nội dung bạn nhập; đây không phải là tìm kiếm toàn bộ cơ sở dữ liệu. Ký hiệu phiên đọc được giữ như trong nguồn; hãy đối chiếu tài liệu gốc để xác nhận mẫu có phù hợp. Lần tìm đầu tiên có thể mất một lúc; các lần sau dùng lại dữ liệu đã đọc. Đây là kết quả tham khảo cho nghiên cứu cá nhân phi thương mại; mức phù hợp của mẫu không được tự động đánh giá. Tài liệu đã tải không được tự động lưu thành tệp.
  - **Lưu trữ bản thảo lịch sử Hàn Quốc AKS**: Tìm trong lưu trữ bản thảo của Học viện Nghiên cứu Hàn Quốc tại archive.aks.ac.kr, mỗi lần một từ hoặc cụm từ. Chỉ đọc trang đầu của nhóm nguyên văn, tối đa 20 dòng; mỗi kết quả hiển thị tiêu đề bản thảo, trích đoạn bản đọc có đánh dấu, niên đại nếu có và liên kết tới trang bản ghi của lưu trữ. Trích đoạn bị cắt ở cả hai đầu và giữ ký hiệu của nguồn; trình xem ảnh và nội dung bản ghi không được tải. Mẫu đã kiểm tra không trùng tiêu đề nào với 561 manifest Wikisource cục bộ, nhưng mức trùng với tư liệu của các viện đã kết nối chưa được đo. Không lưu trữ nội dung nào. Quyền nội dung: chân trang lưu trữ có huy hiệu CC BY-NC-ND 2.0 KR và thông báo All Rights Reserved; việc dùng là cá nhân phi thương mại và không được phân phối lại trích đoạn. Trang bản ghi có nút sao chép trích dẫn.
  - **Tài liệu lịch sử Kyujanggak · nguyên văn**: Tìm trong CSDL tài liệu lịch sử Kyujanggak (GMS) của Đại học Quốc gia Seoul, một từ khóa không dấu cách, tối đa 80 ký tự. Chỉ đọc 20 tham chiếu đầu tiên từ một phản hồi gốc. Hiển thị tiêu đề, số văn bản, các bên, niên đại nguồn, loại và trích đoạn nguyên văn nội tuyến nếu có, tối đa 600 ký tự. Một số kết quả chỉ có siêu dữ liệu; nội dung tải chậm không được yêu cầu. Bản đọc/bản phiên văn có thể chứa chữ Idu, ký hiệu chưa chắc chắn và ký hiệu biên tập; ngôn ngữ và ranh giới câu không được xác định. Không gán kết quả khớp ngữ pháp hay mức đánh giá. Tiêu đề mở trình xem nguyên văn chính thức. Sách cổ, biên niên sử, nghi lễ hoàng gia và nhóm văn bản chính quyền hiện đại nằm ngoài tuyến này. Mức trùng với tư liệu của các viện khác chưa được đo. Không tải hay lưu trang tiếp theo, thêm văn bản bài, hình ảnh hay bản dịch. Chỉ dùng cho nghiên cứu cá nhân phi thương mại; hãy giữ tên Kyujanggak và ghi nguồn văn bản, không phân phối lại.
- **Nhật Bản (5)**:
  - **Thư viện Quốc hội Nhật Bản (NDL)**: Tìm trong văn bản OCR sách cổ của NDL (NDL Lab). NDL chỉ tìm được cụm từ hai ký tự trở lên, nên thành tố dài hơn (hoặc từ dài nhất) được gửi đi và phần còn lại được kiểm tra trên ứng dụng. NDL trả về trích đoạn OCR không có dấu câu, có thể có lỗi nhận dạng và bị cắt ở cả hai đầu; hãy đối chiếu ảnh trang qua liên kết tiêu đề trước khi trích dẫn. 50 sách đầu tiên được kiểm tra. Không lưu trữ nội dung nào.
  - **CSDL văn bản Đại tạng kinh SAT**: Tìm trong CSDL văn bản Đại tạng kinh SAT của Đại học Tokyo bằng một từ hoặc cụm từ. Chỉ đọc nhóm đầu tiên gồm tối đa 30 vị trí dòng khớp; mỗi kết quả hiển thị trang và khoảng dòng Đại Chính, trích đoạn từ khóa trong ngữ cảnh và liên kết chi tiết. Trích đoạn bị cắt ở cả hai đầu, và ký tự chỉ có trên ảnh được giữ là □. Văn bản Đại Chính của SAT trùng với đại tạng CBETA đã kết nối, nên tuyến này thêm tham chiếu dòng chứ không thêm tư liệu mới. Điều khoản phân biệt việc dùng văn bản cho học thuật hoặc phi lợi nhuận; bộ sưu tập hình ảnh là riêng. Không phân trang, không tải trang chi tiết và không lưu trữ.
  - **SHIPS, Đại học Tokyo · tìm kiếm chính thức**: Mở cơ sở dữ liệu đã chọn trong tìm kiếm toàn văn SHIPS của Viện Sử học, Đại học Tokyo trong trình duyệt. Có thể chọn một trong 7 cơ sở dữ liệu toàn văn: Nhật ký cổ (古記録フルテキストデータベース), Dai Nihon Shiryō (大日本史料), Văn thư cổ (古文書), Văn thư thời Nara (奈良時代古文書), Heian ibun (平安遺文), Kamakura ibun (鎌倉遺文) và Nanbokuchō ibun (南北朝遺文). Dùng một từ khóa không có dấu cách. Trang chính thức cung cấp trích đoạn, niên đại nguồn, tham chiếu văn thư hoặc quyển/trang và liên kết ảnh nếu có. Chú thích biên tập và ký tự chưa chắc chắn được giữ như nguồn cung cấp. Kết nối này không tải, không lưu và không xác minh kết quả bên trong CPE. Chỉ dùng cho học thuật; hãy ghi nguồn và tuân thủ hạn chế phân phối lại của Viện.
  - **Thơ Trung Quốc và Nhật Bản CanD · tham chiếu**: Tìm trong trường nội dung của CSDL thơ CanD do Hankeidou tự biên tập trên các bộ sưu tập, kể cả tuyển tập Nhật Bản. Dùng một từ khóa tối đa 10 ký tự, không dấu cách và không ký tự đại diện, với chế độ mở rộng biến thể của nhà cung cấp TẮT. Đọc một phản hồi gốc và hiển thị 20 đề mục thư mục đầu tiên, giữ tiêu đề, tác giả và chỉ dẫn nguồn gốc. Phản hồi còn chứa bài thơ và cách đọc tiếng Nhật; CPE bỏ các phần nội dung và bình chú đó. Không xác minh câu, lần xuất hiện nguyên văn hay kết quả khớp ngữ pháp. Tiêu đề mở trang tổng quan ấn bản của nhà cung cấp, không mở từng bài thơ. Ký tự trả về giữ nguyên; nhà cung cấp tự sửa tự hình và dấu câu. Tác phẩm Trung Quốc trùng một phần với các nguồn hiện có; mức trùng ở cấp viện chưa được đo. Không phân trang, không yêu cầu văn bản riêng và không lưu trữ. Chỉ dùng cho nghiên cứu cá nhân phi thương mại; hãy giữ tên CanD và ấn bản, tôn trọng hạn chế sao chép của nhà cung cấp.
  - **Digital Engishiki (Bảo tàng Lịch sử Dân tộc Nhật Bản) · tìm kiếm chính thức**: Mở công cụ tìm kiếm công khai Digital Engishiki (延喜式) của Bảo tàng Lịch sử Dân tộc Nhật Bản trong trình duyệt. Tìm trong nguyên văn Hán văn và các bản dịch tiếng Nhật hiện đại, tiếng Anh riêng biệt bằng một từ khóa không có dấu cách. Trang nguồn thực hiện lọc phía máy khách ngay trên trình duyệt; CPE chỉ chuẩn bị URL và không tải, không lưu hay khớp kết quả. Văn bản và hình ảnh theo CC BY 4.0; hãy giữ thông tin ghi nguồn (`"Digital Engishiki (National Museum of Japanese History, khirin t)"`).
- **Trung Quốc (1)**:
  - **CSDL văn bia Trung Quốc, Đại học Chiết Giang · tìm bản phiên văn**: Tìm trong trường bản phiên văn của CSDL văn bia Trung Quốc thuộc Đại học Chiết Giang trên 7 bộ sưu tập mặc định. Dùng tìm kiếm HTTP công khai của nhà cung cấp, một từ khóa không dấu cách, và chỉ đọc 15 tham chiếu đầu tiên. Danh sách cung cấp tiêu đề, triều đại, niên đại nguồn, loại hiện vật và số danh mục, không có trích đoạn bản phiên văn. CPE không xác minh ký tự, câu hay kết quả khớp ngữ pháp. Hãy mở tiêu đề để xem bản ghi chính thức. Các tham chiếu trùng lặp trả về vẫn hiển thị. Trang yêu cầu chữ phồn thể; CPE gửi nguyên ký tự bạn nhập. Tư liệu gồm cả giai đoạn muộn và không phải toàn bộ đều được xác lập là Hán văn. Không tải hay lưu trang tiếp theo, nội dung bài hay hình ảnh. Chỉ dùng cho nghiên cứu cá nhân phi thương mại; hãy giữ tên Đại học Chiết Giang và ghi nguồn bản ghi.
- **Đài Loan (4)**:
  - **Đại tạng kinh Hán văn CBETA**: Tìm trực tuyến trong đại tạng kinh CBETA (CBData API). Thành tố dài hơn (hoặc từ dài nhất) được gửi đi, và CBETA trả về trích đoạn khoảng 40 ký tự ở mỗi bên vị trí khớp; phần còn lại được kiểm tra trên ứng dụng. Chú thích xen dòng được bỏ qua. 50 quyển đầu tiên được kiểm tra, tiêu đề mở quyển trong CBETA Online. Văn bản: CBETA Chinese Electronic Tripitaka Collection, CC BY-NC-SA 4.0 (phi thương mại, ghi nguồn, chia sẻ tương tự). Không lưu trữ nội dung nào.
  - **DocuSky · 佛寺志靈應故事**: Tìm trong cơ sở dữ liệu công khai 佛寺志靈應故事 trên DocuSky: các câu chuyện được chọn từ 普陀洛迦新志 và 清涼山志, không phải toàn bộ sách. Một thành tố hoặc một từ được gửi đi; 50 bản ghi đầu tiên trả về được kiểm tra tại đây. Chỉ các đoạn nguyên văn độc lập được tìm; thẻ nghiên cứu và phần nội dung không được hỗ trợ bị loại trừ. Tiêu đề mở trang của dự án. Người biên soạn: 曹德啓; dữ liệu: 中華佛學研究所; nền tảng: 臺灣大學數位人文研究中心. Dùng cho nghiên cứu cá nhân, phi thương mại theo điều khoản của nhà cung cấp. Không lưu trữ nội dung nào. Danh mục cũng liệt kê các mục trùng lặp, cơ sở dữ liệu siêu dữ liệu và cơ sở dữ liệu ngôn ngữ hiện đại hoặc bản dịch. Chỉ các lớp nội dung của cơ sở dữ liệu đã được rà soát mới được kiểm tra; mọi mục khác được đánh dấu là chưa rà soát.
  - **CSDL toàn văn Đài Loan ITH, Academia Sinica**: Tìm trong CSDL toàn văn Đài Loan công khai của Viện Lịch sử Đài Loan, Academia Sinica, bằng một từ hoặc cụm từ. Trang kết quả đầu tiên được đọc; hiển thị tiêu đề, trích đoạn và liên kết của nhà cung cấp. Đây là tìm kiếm để tham khảo, không phải khớp mẫu trên toàn văn. Không phân trang, không tải trang bài và không lưu trữ; hãy ghi nguồn.
  - **Thư viện số Lịch sử Đài Loan · Văn khế cổ**: Tìm trong bộ sưu tập Văn khế cổ của THDL bằng một từ khóa không dấu cách, trên văn bản tài liệu và siêu dữ liệu. Chỉ đọc mười kết quả đầu tiên, giữ nguyên ký tự, trích đoạn và tham chiếu nguồn trả về. Trích đoạn có thể chứa ngữ cảnh bị tách rời, ký tự chưa chắc chắn và tư liệu biên tập; đây là kết quả tham khảo, không phải câu đã xác minh hay kết quả khớp ngữ pháp. Trang chỉ có một kết quả có thể cung cấp thêm văn bản; CPE hiển thị tối đa 600 ký tự. Tiêu đề nguồn mở tài liệu chính thức. Không phân trang, không yêu cầu bài riêng và không lưu văn bản. Hồ sơ hành chính Minh-Thanh và 淡新檔案 (Tan-Hsin) nằm ngoài kết nối này. Hãy trích dẫn THDL và nguồn gốc của từng tài liệu; chỉ dùng cho nghiên cứu cá nhân phi thương mại, không phân phối lại.
- **Việt Nam (1)**:
  - **Kho OCR Hán-Nôm Việt Điện**: Tìm trong công cụ OCR công khai Việt Điện của kho Hán-Nôm Digitizing Vietnam, mỗi lần một từ hoặc cụm từ. Chỉ đọc trang đầu, tối đa 20 kết quả theo trang; mỗi kết quả hiển thị trích đoạn OCR và mở trình xem trang trên Việt Điện. OCR do máy tạo và chưa hiệu chỉnh, nhà cung cấp còn tìm theo các dạng tự hình khác và dạng giản thể/phồn thể, nên một mục trả về chỉ là kết quả tham khảo trừ khi trích đoạn chứa đúng các ký tự bạn đã nhập. Siêu dữ liệu trang và tài liệu có thể thiếu hoặc không đều; quyền đối với hình ảnh và bản ghi tách khỏi tuyên bố dùng cho nghiên cứu của OCR. Không lưu trữ nội dung nào.

---

## Đọc và diễn giải kết quả

Mỗi kết quả xuất hiện dưới dạng một thẻ hiển thị câu chứa mẫu câu cùng ngữ cảnh trong phạm vi văn bản hoặc đoạn trích được cung cấp.

- **Các ô kết quả**:
  - **Ô A / Ô B (Tìm kiếm cặp)**: **Ô A** là phần văn bản nằm xen giữa thành tố thứ nhất và thành tố thứ hai; **Ô B** là phần văn bản đi sau thành tố thứ hai (không phải ranh giới cố định máy móc; có thể liệt kê nhiều đoạn ứng viên).
  - **Các ô định danh (Mẫu DSL)**: Các mẫu nhiều từ hiển thị đầy đủ từng ô định danh được trích theo thứ tự.
- **Phân nhóm và tiêu đề**:
  - Trong Tìm kiếm cặp, công cụ phân chia kết quả thành **Khả năng phù hợp cao theo cấu trúc** và **Khả năng phù hợp thấp hơn theo cấu trúc** dựa trên các dấu hiệu cấu trúc (ví dụ: hai thành tố đứng liền kề không có A, có thành tố thứ hai ở gần hơn bị bỏ qua, hoặc A vượt qua dấu ngắt câu). Trong Tìm kiếm đơn, tất cả các mục khớp hiển thị trong một nhóm **Kết quả** duy nhất. Trong Mẫu DSL, kết quả dùng tiêu đề trung tính **Kết quả khớp** và **Cần rà soát phân loại** (`classification_unreviewed` đánh dấu các mẫu ngoài dạng cặp hai thành tố đơn giản chưa được rà soát cách phân nhóm tự động, không hàm ý ít khả năng hơn).
  - **Cách phân loại này không phải là kết luận ngữ pháp.** Việc đọc và đánh giá ngữ cảnh trực tiếp của nhà nghiên cứu luôn là yếu tố quyết định.
- **Xuất ảnh chụp kết quả (**Xuất kết quả…**)**: Dùng nút **Xuất kết quả…** trên thanh kết quả để ghi ảnh chụp các khung kết quả đang hiển thị (trên mọi bảng sẵn sàng, theo thứ tự sắp xếp hiện hành) thành tệp `CSV` hoặc `JSONL` kèm tệp thông tin nguồn `.meta.json` (ghi tạm an toàn và hỏi xác nhận trước khi ghi đè tệp có sẵn).
- **Sổ rà soát**: Nhấn **Lưu ứng viên** trên thẻ kết quả để lưu giữ qua các lần mở ứng dụng kèm trạng thái rà soát và ghi chú, rồi xuất các mục đã chọn dưới dạng JSONL kèm tệp thông tin nguồn. Chọn hai ứng viên đã lưu để **So sánh hai ứng viên** cạnh nhau: ứng viên cục bộ đối chiếu đoạn trích đã lưu, nguồn, ấn bản, vị trí trong văn bản nguồn và bằng chứng đã ghi, còn ứng viên trực tuyến chỉ lưu tham chiếu thư mục, thông tin trích dẫn và liên kết nguồn (không có đoạn trích hay vị trí; trường thiếu được ghi là chưa ghi nhận). Dùng **Lưu thiết lập tìm kiếm** và **Khôi phục thiết lập tìm kiếm** để lưu và khôi phục cấu hình tìm kiếm; khi đầu vào cục bộ thay đổi hoặc đang mở thư mục khác, bạn có thể chọn mở lại đầu vào đã lưu hoặc áp dụng thiết lập cho đầu vào hiện tại.

---

## Nâng cao: Chạy từ mã nguồn, siêu dữ liệu và dòng lệnh (CLI)

Các nhà nghiên cứu và lập trình viên làm việc từ shell có thể sử dụng toàn bộ gói phần mềm với `uv`. Để cài đặt thủ công `uv`, vui lòng tham khảo [hướng dẫn cài đặt uv chính thức](https://docs.astral.sh/uv/getting-started/installation/).

### Chạy giao diện đồ họa (GUI) từ mã nguồn

```bash
uv sync --locked
uv run --locked cpe gui
# Hoặc mở trực tiếp một thư mục văn bản (thay path/to/texts bằng đường dẫn thực tế):
uv run --locked cpe gui path/to/texts
```

### Tệp siêu dữ liệu thư mục đi kèm (Tùy chọn)

Bạn có thể đặt một tệp phụ bên cạnh tệp văn bản để ghi lại thông tin thư mục (ví dụ: với tệp `my-text.txt`, đặt tên tệp siêu dữ liệu là `my-text.txt.manifest.toml` bằng cách thêm `.manifest.toml` vào sau tên tệp văn bản). Các trường tùy chọn gồm `title`, `edition`, `era`, `year`, và `year_end`. Nếu bỏ trống, tên tệp sẽ được dùng làm tiêu đề, và việc thiếu thông tin niên đại không ảnh hưởng đến việc tìm kiếm.

### Giao diện dòng lệnh (CLI)

Các lệnh CLI xuất ra tệp CSV, JSONL và siêu dữ liệu thực thi. Thay `my-text.txt` bằng đường dẫn tệp thực tế của bạn:

```bash
# Xem các tùy chọn chung
uv run --locked cpe --help

# Thực hiện khớp mẫu câu trên một tệp văn bản UTF-8 riêng lẻ
# (--out results là tiền tố tạo ra results.csv, results.jsonl và results.run.json)
uv run --locked cpe match patterns/mvp.toml my-text.txt --out results

# Tìm kiếm ITKC qua dòng lệnh
uv run --locked python itkc_search.py --help
```

---

### Máy chủ MCP cục bộ (Tích hợp AI)

Phiên bản 3.3 bao gồm máy chủ stdio MCP (Model Context Protocol) cục bộ tùy chọn dựa trên Python SDK v2 chính thức. Máy chủ cung cấp 20 công cụ có cấu trúc bao quát 4 ngăn thư viện cục bộ, toàn bộ 19 bộ điều hợp nguồn trực tuyến, danh mục nhà cung cấp, phiên làm việc theo giai đoạn cho các bộ sưu tập ITKC, tìm kiếm DSL mẫu câu Hán văn, quy trình nhập ngữ liệu an toàn và xuất trực tiếp tệp CSV/JSONL.

```sh
# Khởi động máy chủ MCP qua stdio và cho phép cung cấp văn bản gốc cục bộ
uv run --extra mcp cpe mcp path/to/texts --allow-source-text local

# Cờ kiểm tra trước (thoát sau khi kiểm tra, không mở stdio hay gửi yêu cầu mạng)
uv run --extra mcp cpe mcp --version
uv run --extra mcp cpe mcp path/to/texts --doctor
uv run --extra mcp cpe mcp path/to/texts --print-config
```

Theo mặc định, máy chủ chỉ trả về tham chiếu và siêu dữ liệu. Để trả về văn bản gốc cục bộ, máy chủ phải được khởi động với `--allow-source-text local` sau khi tự xem xét quyền. Truy cập trực tuyến và nhập dữ liệu là riêng biệt: `--enable-online` thêm quyền truy cập nhà cung cấp, `--enable-import` thêm quyền nhập, nhưng không trả về văn bản gốc nếu thiếu quyền `--allow-source-text SOURCE` tương ứng. Không có quyền `all`. Quyền này không phải giấy phép của nhà cung cấp và không bảo đảm tính hợp pháp của xử lý đám mây, câu trả lời của mô hình hoặc việc lưu giữ. Dùng `--version`, `--doctor` và `--print-config` để kiểm tra thiết lập, đường dẫn cục bộ và cấu hình máy chủ trước khi chạy. Xem [Hướng dẫn cài đặt, tùy chọn và công cụ MCP](docs/mcp.md), [Chính sách dữ liệu và cung cấp văn bản gốc](docs/mcp-data-policy.md), [Bảng đối chiếu tính năng](docs/mcp-parity.md) và [Quy trình đối chiếu dị bản và ghi chú nghiên cứu](docs/variant-comparison-workflow.md).

Các gói phân phối Windows độc lập cũng kèm theo tệp cpe-mcp.exe chấp nhận các tham số khởi động tương tự. Máy chủ giao tiếp qua luồng nhập/xuất chuẩn (stdin/stdout), không mở cửa sổ đồ họa hay máy chủ HTTP. Bản triển khai này không tuyên bố đã được chứng nhận tương thích trên các ứng dụng AI thương mại cụ thể (như Claude Desktop, Claude Code) hoặc các dịch vụ trực tuyến bên thứ ba.

---

## Các câu hỏi thường gặp (FAQ)

**Q1. Tệp văn bản của tôi không mở được hoặc không thể đọc.**

A. Chương trình này chỉ đọc các tệp văn bản thuần túy (`.txt`, `.md`) được mã hóa UTF-8. Việc đổi phần mở rộng của tệp Word (`.docx`), HWP hoặc PDF thành `.txt` không biến chúng thành văn bản thuần túy. Vui lòng lưu tài liệu dưới dạng văn bản thuần túy UTF-8 từ các ứng dụng tương ứng.

**Q2. Tìm kiếm không trả về kết quả nào.**

A. Kiểm tra các nguồn và thư mục đã chọn trong phần **Nguồn**. NDL yêu cầu ít nhất một từ khóa từ hai chữ trở lên. Hãy thử một truy vấn đơn giản hơn; không có kết quả không chứng minh mẫu câu không tồn tại trong lịch sử.

**Q3. Xảy ra lỗi khi tìm kiếm trực tuyến.**

A. Kiểm tra kết nối mạng và thông báo lỗi của nguồn. Giới hạn yêu cầu theo phiên của ứng dụng nhằm bảo vệ máy chủ của nhà cung cấp, không phải tuyên bố chính sách của riêng họ. Truy vấn trực tuyến phức tạp cũng phụ thuộc vào các từ khóa bắt buộc có thể gửi đến nhà cung cấp đó hay không. Bạn có thể tải các tài liệu gốc được phép và tìm kiếm chúng trên máy tính.

**Q4. Tệp thực thi Windows (cpe-gui.exe) không mở được.**

A. Tệp `cpe-gui.exe` được biên dịch cho Windows 64-bit (x64). Nếu nhấp đúp không có phản hồi hoặc báo lỗi, hãy đảm bảo rằng [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) đã được cài đặt trên hệ thống của bạn.

---

## Phân phối và Giấy phép

- Kho lưu trữ này bao gồm gói phần mềm thời gian chạy, danh mục dịch thuật, tài nguyên trang web, các trình khởi chạy, khóa phụ thuộc thời gian chạy và giấy phép.
- Thư mục `docs/` chứa các trang web của dự án cho từng ngôn ngữ giao diện, và `discoverability.md` ghi lại cách thức giới thiệu kho lưu trữ và các trang web.
- Phiên bản gói: `3.3`; thẻ Git: `v3.3`. Ảnh chụp nhanh mã nguồn chính xác được ghi lại trong `release.json`.
- Được phát hành theo Giấy phép MIT (`LICENSE`).
- Dự án nghiên cứu cá nhân độc lập, không liên kết với ITKC hay các cơ quan khác. Để tôn trọng bản quyền và điều khoản sử dụng, không có kho ngữ liệu độc quyền, bản dịch hiện đại hay phản hồi API thô nào được đóng gói kèm theo.

## Góp ý và đề xuất

[Gửi góp ý và đề xuất (Google Biểu mẫu)](https://docs.google.com/forms/d/e/1FAIpQLSfcg9NjJil-pxO_e833Xjzo-sVar1YDY8hRIkunBhEZmtPn2A/viewform)
