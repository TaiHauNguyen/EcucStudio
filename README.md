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

Bố cục theo DaVinci Configurator 5 (icon tự vẽ, không dùng tài nguyên của Vector):

| Vùng | Chức năng |
|---|---|
| **Configuration Editors** (trái) | `<Filter>`, các domain gập/mở (Base Services, Communication, Diagnostics, I/O, Memory, Microcontroller, Mode Management, Network Management, Runtime System, Security, Time Synchronization); link mở editor theo module hoặc cả domain; *Basic Editor*, *Project Settings* ở đáy |
| **Editor area** (giữa, dạng tab) | Address line (breadcrumb) + trạng thái validation; cây ngữ cảnh có `<Filter>`; **form** tham số: nhãn (LONG-NAME nếu có) · ô nhập/combobox/checkbox · `dec`/đơn vị · ▾ menu (Set to default, Set/Remove user defined, Create/Delete parameter, Copy Path, Physical Units, Show target, Show properties) · biểu tượng lỗi. Chọn nhóm container (`ComIPdu [168]`) → **grid**. Section Sub-Containers có link *Add* |
| **Properties** (dưới trái) | Tab dọc Description / Status (Derived Value, Preconfigured, Default, Changeable, User defined, Annotations, Path, File) / Definition |
| **Validation · Find · Generation Result · Console** (dưới phải) | Validation: cột ID / Message, "N messages in M categories", nút Validate / DaVinci validation / Solve All và lọc Error/Warning/Info/Acknowledged trên thanh view; Find: tìm container, tên hoặc giá trị tham số; Generation Result: domain → generator → Generated Files |
| **Generate** (Ctrl+G) | Wizard: cây Generation Step có checkbox theo domain, cột Calculation / Validation / Generation cập nhật trực tiếp trong lúc DaVinci chạy, *Properties >>* để chọn DVCfgCmd, target, SWC templates, `--saveProject` |
| Status bar | Thông báo, tiến trình, số lỗi/cảnh báo, configuration phase |

Trạng thái tham số giống DaVinci: nhãn xám = *not set* hoặc read-only; icon khoá = *pre-configured*;
icon mắt xích = *derived* (sửa bằng *Set user defined*); icon người = *user defined*. Đơn vị hiển thị theo
`DV:Unit` (đổi bằng ▾ › Physical Units), luôn lưu theo `DV:BaseUnit`. Nút □ trên mỗi view để phóng to/thu nhỏ.

Phím tắt: Ctrl+O mở, Ctrl+S lưu, Ctrl+Z/Ctrl+Y undo/redo, Alt+←/→ editor trước/sau, F5 validate,
Ctrl+G generate, Ctrl+F Find, Ctrl+L đi tới đường dẫn (nhận cả `/ActiveEcuC/Com/ComGeneral[0:ComX]`),
F2 đổi tên, Del xoá container.

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
