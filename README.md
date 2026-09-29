# EcucStudio

Tool cấu hình ECUC ARXML có giao diện kiểu DaVinci Configurator (Basic Editor), validate theo
rule của DaVinci, và gọi **DaVinci Configurator command line (DVCfgCmd.exe)** để validate/generate code.

## Chạy

```bat
run.bat D:\MyEcu\Project\MyEcu.dpa
rem hoặc
python -m ecucstudio gui D:\MyEcu\Project\MyEcu.dpa
```
Yêu cầu: Python 3.10+ (bản cài từ python.org, có chọn *tcl/tk and IDLE* và *Add python.exe to PATH*)
và gói `lxml`:
```bat
py -m pip install -r requirements.txt
```
`run.bat` tự kiểm tra Python/tkinter/lxml, tự cài `lxml` nếu thiếu và in lỗi ra cửa sổ nếu không chạy được.
Kiểm tra thủ công: `py -m ecucstudio --check`. Lỗi khi khởi động/trong lúc chạy được ghi vào
`%LOCALAPPDATA%\EcucStudio\ecucstudio.log`. Muốn xem lỗi trực tiếp thì chạy `py -m ecucstudio gui` trong cmd.

## Giao diện

| Vùng | Chức năng |
|---|---|
| Cây bên trái | Module → container, nhóm các container nhiều instance (`ComSignal [1034]`), ô tìm kiếm (Enter), menu chuột phải: thêm/đổi tên (F2)/nhân bản/xoá (Del) container |
| Giữa | Thanh địa chỉ (breadcrumb) + **form** tham số của container (double-click/F2 để sửa, chuột phải: default, user-defined, thêm instance, xoá, tới đích reference) hoặc **grid** khi chọn nhóm |
| Phải | Properties: Description / Definition (kiểu, multiplicity, range, literal, đơn vị, config class) / Status (derived, pre-configured, user-defined, annotation, lỗi) |
| Dưới | **Validation** (nhóm theo ID, lọc Error/Warning/Info, nguồn Local/DaVinci/Plugins, Solve / Solve All, Acknowledge), **Console** (output DVCfgCmd), **Generation Result** |

Trạng thái tham số giống DaVinci: *not set* (xám), *default*, *derived* (từ input file — sửa sẽ hỏi
đặt User-Defined), *pre-configured* (khoá), *user-defined*, *calculated* (IS-AUTO-VALUE). Đơn vị
hiển thị theo `DV:Unit` (vd ms) nhưng lưu theo `DV:BaseUnit` (vd s).

Phím tắt: Ctrl+O mở, Ctrl+S lưu, Ctrl+Z/Ctrl+Y undo/redo, F5 validate, Ctrl+G generate,
Ctrl+F tìm, Ctrl+L đi tới đường dẫn (nhận cả dạng `/ActiveEcuC/Com/ComGeneral[0:ComX]`).

## Validation

1. **Basic rules** (tự cài đặt, cùng ID/nội dung với DaVinci): AR-ECUC02008 multiplicity,
   02027/02028 range, 02030/02031 linker symbol, 02039 đích reference sai định nghĩa, 02067 choice,
   02093 đích không active, 03005 enum, 03019 sai định nghĩa, 06052 variant, AR-BSWMD00033
   lệch pre-config, AR-BSWMD00034 lệch published information, AR-GST00021 trùng tên (không phân biệt hoa thường),
   Cfg00020 lệch cấu hình initial (container derived bị xoá, tham số khác giá trị derived),
   Cfg00021 sai kiểu, Cfg00022 thiếu giá trị, Cfg00024 thiếu đích reference, Cfg00028, Cfg00031, Cfg00032.
   Trên project thật dùng để phát triển, kết quả trùng khớp 100% với `DVCfgCmd -v` cho các ID này.
2. **Rule plugin** trong `rules\*.py` (xem `rules\README.md`): ví dụ EST10001 (Com PERIODIC không có
   period), EST20001 (stack task Os không chia hết 8), EST00002 (short name không hợp lệ).
3. **DaVinci thật**: nút *Validate with DaVinci* chạy `DVCfgCmd -v --reportFile ...` và gộp kết quả
   (COMxxxxx, RTExxxxx, …) vào cùng view; double-click để nhảy tới đối tượng.

Tham số User-Defined được hạ Error → Warning như DaVinci. Acknowledge của DaVinci (lưu trong .dpa)
được nhận diện; acknowledge của tool lưu ở `%APPDATA%\EcucStudio\settings.json` (không sửa .dpa).

## Generate code

Đường dẫn DVCfgCmd.exe: *Project › Settings…* (lưu ở `%APPDATA%\EcucStudio\settings.json`). Tool tự dò
`<SIP>\DaVinciConfigurator\Core\DVCfgCmd.exe` và các đường dẫn trong biến môi trường
`ECUCSTUDIO_DVCFGCMD` (phân cách bằng `;`), ưu tiên bản khớp phiên bản DaVinci của SIP.

*Project › Generate with DaVinci…*: chọn DVCfgCmd,
chọn module (hoặc tất cả), target REAL/VTT, SWC template, `--saveProject`. Tool lưu file, (tuỳ chọn)
validate cục bộ các module đã chọn, rồi chạy:
```
DVCfgCmd.exe -p <dpa> -g -m /MICROSAR/Det,... --reportFile <...>\GenerationReport.xml --reportArgs CreateXmlFile -l <log>
```
Output hiện trực tiếp ở Console; kết thúc thì đọc report → tab Generation Result (trạng thái từng
generator, file sinh ra, `FILE_IS_UP_TO_DATE`...). Report/log nằm ở
`%LOCALAPPDATA%\EcucStudio\reports\<project>` (thư mục project không bị ghi thêm).

Lưu ý môi trường của workspace này: MCAL (Adc, Dio, Mcu, Port, …) generate qua EB tresos cần license
tresos; MemMap cần `..\Board\AsrCommon\inc\MemMap_User.h`. Generate các module MICROSAR riêng lẻ vẫn chạy được.

## Command line

```bat
python -m ecucstudio info     Project.dpa
python -m ecucstudio validate Project.dpa [--modules Com,Det] [--json out.json] [--davinci]
python -m ecucstudio set      Project.dpa "/ActiveEcuC/Det/DetGeneral[0:DetVersionInfoApi]" true --save
python -m ecucstudio generate Project.dpa -m /MICROSAR/Det [--gen-type REAL]
```
`validate` trả exit code 1 nếu còn Error chưa acknowledge (dùng được trong CI).

## An toàn dữ liệu

- Chỉ ghi các file ECUC đã sửa; phần không sửa giữ nguyên từng byte (đã test trên 70 file của project).
- Ghi file mới qua file tạm rồi thay thế, tạo `.bak` (tắt được trong Settings).
- Cảnh báo nếu file bị chương trình khác (DaVinci) sửa sau khi mở; sau khi DaVinci chạy với
  `--saveProject` tool đề nghị nạp lại.
- Không sửa `.dpa`, BSWMD hay SIP.

## Kiến trúc

```
ecucstudio/
  arxml.py      XML helpers, ghi file giữ định dạng
  bswmd.py      kho định nghĩa BSWMD (index có cache, nạp lười, refined module, pre-config)
  project.py    đọc .dpa, model ECUC sửa tại chỗ + undo/redo, đổi tên cập nhật reference
  validation/   engine + basic rules (ID DaVinci)
  rulekit.py    API viết rule plugin
  davinci.py    dò DVCfgCmd, dựng command line, chạy nền, parse DaVinciExecutionReport
  session.py    gom project/definition/model/validation, trạng thái tham số
  gui/          tkinter: app, tree, editor, properties, validation_view, console, dialogs
rules/          rule plugin (*.py)
tests/          unittest trên bản sao project thật
```
Test: `set ECUCSTUDIO_TEST_DPA=<project>.dpa` rồi `python -m unittest discover -s tests -v`
(các test trên project thật sẽ được bỏ qua nếu không đặt biến này).
