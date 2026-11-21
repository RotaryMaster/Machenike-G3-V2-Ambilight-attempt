# ZC32RF1M — manufacturer and hardware documentation leads (2026-11-21)

This is a **read-only research lead** for the Machenike G3 V2 Ambilight project, not a claim that its encrypted FOT firmware has been decrypted or that a public toolchain is available.

## Trigger

User supplied this EEVblog thread for possible identification of the MCU logo:

https://www.eevblog.com/forum/microcontrollers/which-brand-is-this-microcontroller-logo-with-2-x/

**Verification status of linked thread:** EEVblog prevented automated retrieval (restricted URL / robots). Search-engine results did not independently reproduce the topic content. We have **not confirmed** that its "two X" logo belongs to ZhiXu, nor what model the thread discusses. Avoid asserting a logo match until the thread is directly inspected and compared with a photograph of the ZC32RF1M.

## Stronger independent manufacturer identification

An FCC internal-photo exhibit for a **different** game controller, FCC ID 2AYJK-C2 (Shenzhen Warsong Technology), explicitly reads:

> RF CHIP: Model: ZC32RF1M — Manufacturer: ZHIXU TECHNOLOGY.

Independent transcript (HTML): https://device.report/m/f762c4fd93632c35cdd85bdeba1214b50f66fd8613c60fa1a26f4b02254368fa

This supports identifying the MCU manufacturer, not a pin-by-pin identification of our G3 V2 board.

ZhiXu Technology official Chinese website:

- Company description: https://www.smart-one.cn/page?p=about_company
- Technical service: https://www.smart-one.cn/service-support
- Contact: https://www.smart-one.cn/contact

ZhiXu states that it develops chips, gamepad boards/solutions and Bluetooth modules; its **service page explicitly mentions SDKs and technical guidance**, but does not say a ZC32RF1M SDK is publicly downloadable. Public contact from its site: **info@smart-one.cn**.

## Secondary independent leads

- A patent for another controller uses **ZC32RF1M LQFP48**, with a signal-processing diagram and circuit interconnections (FIG.11):
  https://patents.google.com/patent/CN112439189A/en
  **Not our exact package:** G3 V2 has ZC32RF1M **QFN40** per previous teardown observations. Do **not** use this different package's pin assignments or inferred debug contacts to probe the G3 V2.
- An additional independent teardown identifies ZC32RF1M and its supplier:
  https://doit-man.com/teardowns/bteam-specter-%E6%B2%9B%E5%BD%B1-%E7%84%A1%E7%B7%9A%E9%81%8A%E6%88%B2%E6%89%8B%E6%8A%8A-%E5%AE%8C%E6%95%B4%E6%8B%86%E8%A7%A3-anjimd
- JLCPCB part-library entry describes a QFN40_5X5 footprint but **not** MCU memory endurance, ISP protocol or internal firmware algorithms:
  https://jlcpcb.com/partdetail/JLCPCBAssembly-ZC32RF1M/C9900059865

## Engineering impact / next evidence

Useful questions for the silicon vendor, without sending device IDs, proprietary firmware or private data:

1. Is there a public **ZC32RF1M QFN40 datasheet**, reference manual or pinout?
2. What is the **core architecture** and what debug/bootloader interfaces (SWD/JTAG/UART/ISP) exist? Are reads protected?
3. Is there a ZC32RF1M SDK, public demo/reference firmware, or development/evaluation kit?
4. What is the **nonvolatile-memory endurance** and whether per-write RGB setting updates would program Flash?
5. Is FOT update encryption implemented in MCU ROM bootloader, firmware bootloader, an external tool or device-specific security engine? Can documentation for the update image layout be shared?

Until answered, do not infer a firmware decryption key, volatile RGB opcode, flash endurance number, or safe physical pinout.

**Decision:** Real value from this link is initiating a targeted ZhiXu manufacturer-documentation track. It does not by itself enable firmware decryption or greenlight RGB streaming using 0x37. No hardware experiments or firmware writes were performed.
