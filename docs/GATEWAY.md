# Hướng dẫn sử dụng: Gateway CAN ↔ Ethernet (PduR)

Tool sinh **System Description** (file network ARXML) có gateway PduR giữa CAN và Ethernet. Import file này
vào DaVinci Configurator thì DaVinci tự tạo PduR routing path, SoAd PduRoute / SocketRoute (header ID) và
CanIf PDU, không phải cấu hình ECUC bằng tay.

Có hai cách dùng: **giao diện** (mục 1–7) hoặc **command line** (mục 8). Cả hai ra cùng một kết quả.

Tool dùng được cho ba tình huống:

| Bạn đang có | Kết quả |
|---|---|
| File network ARXML **có** Ethernet cluster | gateway được gộp vào file đó |
| File network ARXML **chỉ có CAN** | tool tạo thêm phần Ethernet (mục 3.1) |
| **Chỉ có file DBC**, không có file network | tool tạo một file network ARXML **mới hoàn toàn** (mục 2.1) |

---

## 0. Chuẩn bị

- Python 3.10+ cùng các gói `lxml` và `cantools` (`py -m pip install -r requirements.txt`).
  `run.bat` tự kiểm tra và tự cài nếu thiếu.
- File network ARXML mà project đang dùng (ví dụ export từ PREEvision), **nếu có**. Có Ethernet cluster thì
  gateway được gộp vào cluster đó. **Chưa có** Ethernet cluster (file chỉ có CAN) thì tool tự tạo cluster, kênh
  (VLAN), controller, connector và địa chỉ IP của ECU, xem mục 3.1. **Không có file network** thì bỏ trống, xem
  mục 2.1.
- File DBC của các bus CAN cần gateway, và tên node của ECU gateway trong DBC. Encoding được tự nhận dạng:
  UTF-8 có/không BOM, UTF-16, cp1252 (mặc định của tool Vector).
- Thông tin Ethernet: VLAN, port UDP/TCP của ECU, IP và port của node bên kia (hoặc dùng socket đã có
  trong file network). Nếu tool phải tạo kênh mới: thêm IP của ECU (và MAC nếu muốn).

## 1. Mở tool

| Cách | Lệnh |
|---|---|
| Trong EcucStudio | menu **Tools → CAN-Ethernet Gateway Generator…** |
| Chạy riêng | `run.bat gateway` (thêm đường dẫn `gateway.json` để mở cấu hình đã lưu) |
| Command line | `python -m ecucstudio gateway gui [gateway.json]` |

Thanh công cụ: **New**, **Open…**, **Save**, **Save As…** (cấu hình `.json`), **Analyze**,
**Generate network ARXML**.

## 2. Tab **Input**

1. **Base system description**: chọn file network ARXML đang dùng. Tool đọc file và hiện tóm tắt
   (schema, số ECU, số kênh Ethernet và CAN).
2. **Output file**: file sẽ được tạo ra, mặc định `<base>_gateway.arxml`.
3. **Gateway ECU**: để `<auto>` nếu file chỉ có một ECU, hoặc chọn ECU làm gateway.
4. **Add DBC…**, mỗi bus CAN một dòng:

   | Trường | Ý nghĩa |
   |---|---|
   | DBC file | file DBC của bus |
   | Gateway node | node là ECU gateway trong DBC; danh sách hiện số message node đó nhận / gửi |
   | CAN channel | kênh CAN có sẵn trong base (tool tìm frame theo CAN ID và dùng lại), hoặc `<new CAN cluster from DBC>` để tạo bus mới |
   | Bus name | tên `{bus}` dùng khi đặt tên phần tử (mặc định: tên kênh hoặc `DBName` của DBC) |
   | Baud rate / CAN FD data baud rate | chỉ dùng khi tạo cluster mới; bỏ trống thì lấy từ DBC (mặc định 500000, FD 2000000) |
   | RX messages: CAN -> ETH | route các message node **nhận** lên Ethernet |
   | TX messages: ETH -> CAN | route các message node **gửi** từ Ethernet xuống CAN |
   | include NM / include diagnostic | mặc định bỏ message NM (`NmAsrMessage`) và diagnostic (`DiagRequest/Response/State`) |

   Sửa: double-click dòng hoặc **Edit…**; xoá: **Remove**.

### 2.1 Chỉ có file DBC (không có file network)

1. Để **trống** ô *Base system description*.
2. **Add DBC…** như trên. Tool tự đề xuất *Output file* là `<DBName>_network.arxml` cạnh file DBC.
3. **Gateway ECU**: tên ECU-INSTANCE của file mới. Mặc định là node gateway trong DBC; gõ tên khác nếu project
   DaVinci dùng tên ECU khác.
4. **Schema (new file)**: `AUTOSAR_00052` (mặc định, DaVinci 5.31). Với DaVinci 5.24 chọn `AUTOSAR_00049` hoặc
   cũ hơn.
5. Tab **Ethernet**: kênh tự chọn `<create new channel (VLAN)>`. Nhập **VLAN id**, **ECU IP**, rồi port/IP cho hai
   chiều như mục 3.

File mới gồm:

- ECU-INSTANCE `/Topology/HardwareComponents/<ECU>`;
- SYSTEM `/System/System` (CATEGORY ECU_EXTRACT);
- CAN cluster của bus (từ DBC);
- Ethernet cluster / kênh / controller / connector / IP;
- socket, header ID, GATEWAY;
- base type, PDU, signal.

Các package theo bố cục của PREEvision.

> **Lưu ý khi import vào DaVinci.** Bus CAN trong file mới **chỉ chứa các message được route**. Message bị tắt
> (NM, diagnostic, message bỏ chọn) và các message node không gửi/nhận đều không có trong file.
> - Nếu ECU chỉ làm gateway cho bus này: import file mới **thay cho** DBC. Đừng import thêm cùng DBC đó, vì bus
>   sẽ xuất hiện hai lần.
> - Nếu ECU còn cần các message khác của bus (NM, diagnostic, signal cho SWC): chế độ dùng chung với DBC đang
>   import chưa được hỗ trợ. Hãy báo để bổ sung.

## 3. Tab **Ethernet**

| Trường | Ý nghĩa |
|---|---|
| Ethernet channel (VLAN) | kênh / VLAN để gửi và nhận PDU gateway, hoặc `<create new channel (VLAN)>` (mục 3.1). Kênh mà ECU chưa nối vào có ghi chú `ECU not connected` |
| ECU connector | connector Ethernet của ECU trên VLAN đó (`<auto>` = connector đầu tiên; tool cảnh báo nếu có nhiều). `<create new>` khi ECU chưa nối vào kênh |
| Local endpoint (ECU IP) | địa chỉ IP của ECU cho socket mới (`<auto>` = endpoint của connector) |
| Protocol | UDP (mặc định) hoặc TCP (chọn TCP role CONNECT / LISTEN) |
| Header id set | nơi lưu các SO-CON-I-PDU-IDENTIFIER: `<auto>` = set đang dùng cho socket đã chọn (hoặc tạo `CanEthGateway_Ids`), chọn set có sẵn, hoặc gõ tên set mới |

### 3.1 Khung **New channel / ECU connection**

Chỉ dùng trong ba trường hợp (dòng chữ xám ở cuối khung cho biết trường hợp nào đang áp dụng):

1. File base **chưa có Ethernet cluster**: kênh tự chọn là `<create new channel (VLAN)>`, tool tạo cluster mới.
2. Chọn `<create new channel (VLAN)>` để thêm một **VLAN mới** vào cluster đã có.
3. Chọn một kênh có sẵn mà **ECU chưa nối vào**: tool tạo connector cho ECU trên kênh đó.

| Trường | Ý nghĩa |
|---|---|
| Cluster | cluster chứa kênh mới: `<auto>` = cluster duy nhất có sẵn, hoặc tạo cluster mới `EthernetCluster` nếu file chưa có |
| Channel name | tên kênh mới (bỏ trống = `Channel_VLAN<id>` hoặc `Channel_Untagged`) |
| VLAN id | 1..4094; **bỏ trống = kênh untagged**. Trùng VLAN đã có trong cluster thì báo lỗi (hãy chọn kênh đó) |
| ECU IP / netmask | **bắt buộc** khi tạo kênh hoặc connector mới: tạo NETWORK-ENDPOINT của ECU (IP tĩnh). Nếu IP đã có trong kênh thì dùng lại |
| Controller | controller Ethernet của ECU cho connector mới: `<auto>` = controller có sẵn của ECU (coupling port được thêm VLAN membership), hoặc tạo `CT_<ECU>_Eth` nếu ECU chưa có |
| MAC (new controller) | MAC-UNICAST-ADDRESS của controller mới (không bắt buộc) |

Tool tạo ra:

- ETHERNET-CLUSTER (khi chưa có);
- ETHERNET-PHYSICAL-CHANNEL (CATEGORY WIRED, VLAN với VLAN-IDENTIFIER nếu có);
- NETWORK-ENDPOINT của ECU;
- ETHERNET-COMMUNICATION-CONTROLLER (COUPLING-PORT có VLAN-MEMBERSHIP SENT-TAGGED hoặc SENT-UNTAGGED);
- ETHERNET-COMMUNICATION-CONNECTOR, có NETWORK-ENDPOINT-REFS, và được nối vào COMM-CONNECTORS của kênh.

Từ các phần tử này, DaVinci suy ra Eth controller (MAC), EthIf controller (`EthIfVlanId`), TcpIp controller,
local address (IP tĩnh, netmask) và nhóm socket SoAd.

### 3.2 Socket hai chiều

Hai khung cho hai chiều:

- **CAN -> ETH**: ECU gửi PDU lên Ethernet.
- **ETH -> CAN**: ECU nhận PDU từ Ethernet.

Trong mỗi khung:

| Trường | Ý nghĩa |
|---|---|
| Local socket | socket có sẵn của ECU, hoặc `<create new>` |
| new socket name / port | khi tạo mới: tên (bỏ trống = `SA_<ECU>_CanGw_Tx` / `_Rx`) và **port của ECU** |
| Remote socket | socket có sẵn của node bên kia, hoặc `<create new>` |
| remote IP / port | khi tạo mới: **IP và port của node bên kia** (IP đã có trong VLAN thì dùng lại endpoint đó) |
| remote socket name | tên socket remote mới (bỏ trống = tên mặc định) |
| Socket connection name | tên STATIC-SOCKET-CONNECTION mới (bỏ trống = dùng connection có sẵn tới remote đó, hoặc `<local>_to_<remote>`) |

Hai chiều có thể dùng chung một socket.

## 4. Tab **Options & Naming** (không bắt buộc)

- **Set bit 31 for extended CAN ids**: luôn đặt bit 31 cho CAN ID extended (kiểu `Can_IdType`).
  Mặc định tắt, tức header = CAN ID đệm 0.
- **Ethernet PDU content**:
  - cùng layout signal với PDU CAN (mặc định, giống file PREEvision);
  - hoặc PDU không có signal.
- **Timing of CAN PDUs sent by the gateway**:
  - `event`: gửi khi nhận được từ Ethernet (mặc định);
  - hoặc lấy cycle time từ DBC.
- **Add new elements to the SYSTEM**: thêm phần tử mới vào FIBEX-ELEMENTS (mặc định bật).
- **Short-name patterns**: mẫu đặt tên. Dùng được các trường `{bus} {msg} {sig} {ecu} {node} {canid}
  {frame} {pdu} {signal} {triggering} {connector} {eth_pdu}`; mẫu cho phần Ethernet mới (cluster, channel,
  controller, connector, endpoint) dùng `{ecu} {vlan} {vlan_id}` với `{vlan}` = `VLAN<id>` hoặc `Untagged`.
  Mặc định giống converter DBC của Vector, ví dụ:
  - frame / PDU CAN: `{msg}_o{bus}`
  - PDU Ethernet: `{msg}_o{bus}_Eth`
  - triggering: `{pdu}_PT`
  - kênh / connector Ethernet mới: `Channel_{vlan}` / `CN_{ecu}_{vlan}`

## 5. **Analyze**: xem và chỉnh route

Bảng route (một dòng là một message):

| Cột | Ý nghĩa |
|---|---|
| Enabled | `yes` / `no` (dòng xám là route bị tắt) |
| Direction | `CAN->ETH` hoặc `ETH->CAN` |
| CAN ID, Frame, Length, Cycle ms | thông tin từ DBC (hoặc từ base nếu frame đã có) |
| CAN PDU / Ethernet PDU | tên PDU hai đầu gateway |
| Header ID | SoAd header ID (dòng cam = đã tự thêm cờ vì trùng) |
| Remark | lý do tắt, frame dùng lại từ base, 1:N / N:1, chênh lệch độ dài… |

Thao tác trên bảng:

- **Double-click**: bật/tắt route, nhập header ID tay (ví dụ `0x123`), đổi tên PDU Ethernet.
- **Space**: đảo bật/tắt các dòng đang chọn.
- **Chuột phải**: Enable / Disable / Edit… / Reset overrides.

Mọi thay đổi trên bảng được lưu vào cấu hình và áp dụng lại mỗi lần Analyze.

Khung thông báo bên dưới:

- **ERROR**: chưa generate được (ví dụ thiếu port, IP sai, header ID nhập tay bị trùng).
- **WARNING**: vẫn generate được, nhưng cần đọc. Ví dụ: header ID bị thêm cờ, ECU có nhiều connector,
  route N:1, độ dài PDU trong DBC khác trong base.
- **INFO**: ví dụ dùng lại kênh CAN cùng tên.

### Quy tắc header ID

- Header ID = CAN ID đệm 0 thành 32 bit: `0x123` → `0x00000123`, `0x18FF1234` → `0x18FF1234`.
- Header ID chỉ cần duy nhất ở **phía nhận** trên cùng socket:
  - chiều CAN→ETH: so với các PDU đang gửi từ socket local đó, và các PDU socket remote đang nhận;
  - chiều ETH→CAN: so với các PDU socket local đang nhận;
  - tính cả các PDU có sẵn trong file base.
- Nếu trùng: tool đặt cờ `k << 29` (k = 1..7, ở bit 29..31 mà CAN ID 29 bit không dùng), lấy giá trị nhỏ nhất
  còn trống, rồi báo warning. Ví dụ `0x100` đã có → `0x20000100`; nếu cả giá trị đó cũng đã có → `0x40000100`.
- Header ID nhập tay không bao giờ bị đổi; nếu trùng thì báo ERROR.

## 6. **Generate network ARXML**

Tool tạo hai file:

- **file output**: file base cộng các phần tử mới. Phần còn lại của file giữ nguyên từng byte, phần tử mới
  được ghi đúng thứ tự schema AUTOSAR.
- **`<output>_gateway_routes.csv`**: bảng route (mở bằng Excel), dùng để kiểm tra hoặc gửi cho người khác.

Nếu output trùng với file base, tool hỏi lại và giữ bản `.bak`.

Bấm **Save** để lưu cấu hình `gateway.json` (đường dẫn tương đối). Khi DBC hoặc base thay đổi, mở lại cấu hình
rồi Generate lại **từ file base gốc**. Nếu lỡ chạy trên chính file output, route đã có sẽ được bỏ qua
(Remark: `already routed …`), không bị nhân đôi.

### Phần tử được tạo

| Phần | Phần tử |
|---|---|
| CAN (frame mới) | CAN-FRAME, I-SIGNAL-I-PDU, I-SIGNAL, SYSTEM-SIGNAL, CAN-FRAME-TRIGGERING, PDU-TRIGGERING, I-SIGNAL-TRIGGERING, FRAME-PORT / I-PDU-PORT trên connector CAN của ECU |
| CAN (bus mới) | thêm CAN-CLUSTER + kênh, CAN controller và connector trong ECU |
| CAN (frame có sẵn) | chỉ thêm FRAME-PORT / I-PDU-PORT nếu ECU chưa có |
| Ethernet | I-SIGNAL-I-PDU cùng độ dài, PDU-TRIGGERING + I-PDU-PORT trên connector Ethernet, SO-CON-I-PDU-IDENTIFIER (HEADER-ID), tham chiếu trong STATIC-SOCKET-CONNECTION, và SOCKET-ADDRESS / NETWORK-ENDPOINT / STATIC-SOCKET-CONNECTION khi tạo mới |
| Ethernet (kênh mới, mục 3.1) | ETHERNET-CLUSTER, ETHERNET-PHYSICAL-CHANNEL + VLAN, NETWORK-ENDPOINT của ECU, ETHERNET-COMMUNICATION-CONTROLLER + COUPLING-PORT, ETHERNET-COMMUNICATION-CONNECTOR |
| Gateway | I-PDU-MAPPING trong GATEWAY của ECU (tạo `Gateway_<ECU>` nếu chưa có) |
| System | tham chiếu phần tử mới trong FIBEX-ELEMENTS |

## 7. Import vào DaVinci Configurator

1. Trong **Input Files** của project:
   - có file network cũ: thay file đó bằng file output (hoặc ghi đè file cũ như ở mục 6);
   - file mới tạo từ DBC (mục 2.1): thêm file vào danh sách system description và gán ECU instance là tên ECU
     đã chọn. Không import lại DBC của bus đó.
2. Chạy **Update** project. DaVinci tạo:
   - **PduR**: `PduRRoutingPath` tên `Gateway_<ECU>_…`, nối CanIf ↔ SoAd;
   - **SoAd**: `SoAdPduRoute` (`SoAdTxPduHeaderId`), `SoAdSocketRoute` (`SoAdRxPduHeaderId`),
     socket connection group bật `SoAdPduHeaderEnable`;
   - **CanIf**: RX / TX PDU với CAN ID và kiểu ID (Standard / Extended, FD).
3. **Solve** các tham số chỉ có trong ECUC (giống mọi route import từ system description):

   | Rule | Tham số |
   |---|---|
   | PDUR13200 | `PduRPduLengthHandlingStrategy` của routing path IF không buffer |
   | PDUR10510, CANIF10034 | `PduRDestPduDataProvision = PDUR_DIRECT` cho đích CanIf |
   | SOAD01616 / 01698 / 01736 | `SoAdTxIfTriggerTransmit`, `SoAdTxIfOptimized`, `SoAdTxIfOptimisticTransmit` |
   | SOAD01502 | handle ID (`SoAdRxPduId`) |

4. Validate rồi generate như bình thường.

## 8. Command line

```bat
:: 1. xem file base có ECU, VLAN, connector, IP, socket nào; DBC có node nào
python -m ecucstudio gateway inspect --base network.arxml --dbc Body.dbc

:: 2. tạo file cấu hình mẫu
python -m ecucstudio gateway template --base network.arxml --dbc Body.dbc --node GwEcu --channel VLAN60 -o gateway.json
:: chỉ có DBC, không có file network (thêm --ecu <tên> nếu ECU instance khác tên node, --schema AUTOSAR_00049 cho DaVinci 5.24)
python -m ecucstudio gateway template --dbc Body.dbc --node GwEcu --vlan 20 --ecu-ip 10.0.20.1 -o gateway.json
:: file base chưa có Ethernet (hoặc thêm VLAN mới với --new-channel): cho VLAN và IP của ECU
python -m ecucstudio gateway template --base can_only.arxml --dbc Body.dbc --node GwEcu --vlan 20 --ecu-ip 10.0.20.1 -o gateway.json

:: 3. điền phần Ethernet trong gateway.json (xem mục 9), rồi xem trước kết quả (không ghi file)
python -m ecucstudio gateway plan gateway.json

:: 4. ghi file network mới + CSV (exit code 1 nếu còn ERROR)
python -m ecucstudio gateway generate gateway.json [-o network_gw.arxml] [-v]
```

## 9. Tham khảo file cấu hình `gateway.json`

```json
{
  "base": "network.arxml",
  "output": "network_gateway.arxml",
  "ecu": "",
  "schema": "AUTOSAR_00052",
  "buses": [
    {
      "dbc": "Body.dbc",
      "node": "GwEcu",
      "channel": "",
      "new_channel": false,
      "bus": "",
      "baudrate": null,
      "fd_baudrate": null,
      "rx": true,
      "tx": true,
      "include_nm": false,
      "include_diag": false,
      "messages": {
        "DoorStatus": {"enabled": false},
        "EngineData": {"header_id": "0x1100", "eth_pdu": "EngineData_Eth"}
      }
    }
  ],
  "ethernet": {
    "channel": "VLAN60",
    "connector": "",
    "local_endpoint": "",
    "protocol": "UDP",
    "id_set": "",
    "new_channel": false,
    "cluster": "",
    "vlan_id": null,
    "channel_name": "",
    "ecu_ip": "",
    "ecu_netmask": "255.255.255.0",
    "controller": "",
    "mac": "",
    "can_to_eth": {"local_port": 50100, "remote_ip": "10.0.10.2", "remote_port": 50100},
    "eth_to_can": {"local_socket": "SA_GwEcu_Rx", "remote_socket": "SA_Tester_Tx"}
  },
  "header": {"extended_flag": false, "flag_shift": 29},
  "options": {"eth_signals": "copy", "can_tx_timing": "event", "add_fibex": true}
}
```

- Đường dẫn tương đối tính từ thư mục chứa file `.json`.
- `base` để trống = chỉ có DBC: tạo file mới. Khi đó `ecu` là tên ECU-INSTANCE mới (trống = node của DBC đầu
  tiên), `schema` là schema của file mới.
- ECU, kênh, connector, socket có thể ghi bằng **đường dẫn AUTOSAR** hoặc **tên ngắn**. Kênh Ethernet còn ghi
  được dạng `VLAN60`. Trường bỏ trống là tự dò.
- Mỗi chiều trong `ethernet`:
  - dùng socket có sẵn: `local_socket` + `remote_socket`;
  - tạo mới: `local_port` + `remote_ip` + `remote_port` (thêm `local_name`, `remote_name`,
    `remote_endpoint`, `remote_netmask`, `connection_name` nếu muốn).
- Kênh mới / ECU chưa nối (mục 3.1): `new_channel`, `cluster`, `vlan_id` (`null` = untagged), `channel_name`,
  `ecu_ip`, `ecu_netmask`, `controller`, `mac`. Base không có Ethernet thì tự tạo kênh, chỉ cần `ecu_ip`
  (và `vlan_id` nếu là VLAN).
- `messages`: chỉnh từng message theo tên trong DBC. `enabled` tắt/bật route, `header_id` đặt header ID tay,
  `eth_pdu` đổi tên PDU Ethernet.
- `naming` (không ghi thì dùng mặc định): các mẫu tên như ở tab Options & Naming.

## 10. Lỗi thường gặp

| Thông báo | Cách xử lý |
|---|---|
| `Select the output file (there is no base file)` | chưa chọn *Output file* khi không có file base |
| `Enter the IP address of <ECU> on <channel>` | tool cần tạo kênh / connector mới: nhập **ECU IP** ở khung New channel / ECU connection |
| `… already has VLAN <n> (…)` | VLAN đó đã có: chọn kênh có sẵn thay vì `<create new channel (VLAN)>` |
| `the remote IP … is the IP address of <ECU>` | IP remote trùng IP của ECU: nhập IP của node bên kia |
| `several Ethernet clusters` | chọn **Cluster** cho kênh mới |
| `Select the gateway ECU` | file có nhiều ECU: chọn **Gateway ECU** |
| `Select the Ethernet channel (VLAN)` | ECU nối với nhiều VLAN: chọn kênh ở tab Ethernet |
| `enter the local UDP port` / `enter the remote UDP port` / `enter the remote IP address` | nhập đủ port, IP cho socket mới, hoặc chọn socket có sẵn |
| `the connector has no network endpoint` | chọn **Local endpoint** (IP của ECU) |
| `header id 0x… is already used by …` (ERROR) | header ID nhập tay bị trùng: đổi giá trị hoặc xoá để tool tự gán |
| `node '…' is not in …` | tên node không có trong DBC: chọn lại ở hộp thoại DBC |
| `… is not a valid DBC file: …` | file không phải DBC hoặc bị hỏng; xem dòng/cột trong thông báo. (Bản cũ báo `Invalid syntax at line 1, column 1: ">>!<<ï»¿VERSION"` với DBC lưu kèm BOM UTF-8 — đã sửa, cập nhật tool bằng `git pull`) |
| Warning `remote endpoint … is an address of <ECU> itself` | IP remote đang là IP của chính ECU: nhập IP của node bên kia |
| Warning `… N:1 route` | PDU CAN đó đã là đích của một route khác; N:1 chỉ có trong MICROSAR dạng extension, tắt route nếu không cần |

## Giới hạn hiện tại

- Route 1:1. PDU CAN đã có route trong base thì được thêm đích Ethernet (thành 1:N); chưa cấu hình được
  N:1 / 1:N trong tool.
- Message multiplexed và PDU không phải I-SIGNAL-I-PDU (ví dụ SecOC) được route nguyên PDU, không có signal.
- TCP: tạo TCP-TP-PORT và TCP-ROLE, các tham số TCP khác dùng mặc định của DaVinci.
