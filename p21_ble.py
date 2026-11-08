#!/usr/bin/env python3
"""Print label images on a Nelko P21 thermal label printer over Bluetooth LE.

Why LE: on macOS the P21's Bluetooth Classic serial port (/dev/cu.P21) often
connects without passing any data. Bluetooth LE works reliably, and it's the
link the official phone app uses.

Protocol notes (see README "How it works"):
  * GATT service ff00: write commands to ff02 (write-without-response),
    replies arrive as notifications on ff01, ff03 may notify flow-control credits.
  * Each TSPL command line must arrive in a single write. Jobs sent in 20-byte
    chunks are accepted and silently never printed, so jobs go out in 180-byte
    chunks (MTU 185) paced 0.06 s apart.
  * Status ESC sequences need a trailing CRLF.
Job building (TSPL SIZE/GAP/DENSITY/BITMAP/PRINT) is reused from p21_print.py.

The whole run uses one LE connection, so the printer can't drop out between labels.

Examples:
    python p21_ble.py --status                                 # printer + roll
    python p21_ble.py --labels-dir examples/breaker-panel/labels L1
    python p21_ble.py --labels-dir examples/breaker-panel/labels --all
    python p21_ble.py my_label.png --copies 3                  # any image file
    python p21_ble.py --selftest                               # built-in test page
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from PIL import Image

import p21_print as p21

HERE = Path(__file__).resolve().parent
CMD_CHAR = "0000ff02-0000-1000-8000-00805f9b34fb"     # write-without-response: commands in
REPLY_CHAR = "0000ff01-0000-1000-8000-00805f9b34fb"   # notify: answers out
CREDIT_CHAR = "0000ff03-0000-1000-8000-00805f9b34fb"  # notify: flow-control credits
CHUNK = 180         # bytes per write; TSPL lines must not be split across writes
CHUNK_DELAY = 0.06  # seconds between writes
DPI = 203           # P21 head resolution (8 dots/mm)


class P21LE:
    """One LE session with the printer: chunked writes, notification-backed replies."""

    def __init__(self, client: BleakClient, debug: bool = False,
                 chunk: int = CHUNK, delay: float = CHUNK_DELAY) -> None:
        self.client = client
        self.chunk = chunk
        self.delay = delay
        self.debug = debug
        self.replies: asyncio.Queue[bytes] = asyncio.Queue()
        self.credits = 0              # packets the printer has said we may still send
        self.credit_seen = False      # True once ff03 has granted anything
        self.credit_event = asyncio.Event()

    def _log(self, text: str) -> None:
        if self.debug:
            print(f"  [{asyncio.get_running_loop().time():9.3f}] {text}")

    def _on_reply(self, _char, data: bytearray) -> None:
        self._log(f"ff01 << {bytes(data).hex()}")
        self.replies.put_nowait(bytes(data))

    def _on_credit(self, _char, data: bytearray) -> None:
        data = bytes(data)
        self._log(f"ff03 << {data.hex()}")
        if len(data) >= 2 and data[0] == 0x01:        # 01 NN: NN more packets allowed
            self.credits += data[1]
            self.credit_seen = True
            self.credit_event.set()

    async def start(self) -> None:
        """Subscribe to the reply and credit characteristics."""
        self._log(f"MTU {self.client.mtu_size}")
        await self.client.start_notify(REPLY_CHAR, self._on_reply)
        try:
            await self.client.start_notify(CREDIT_CHAR, self._on_credit)
        except Exception as error:  # credits are optional; pacing does the work
            self._log(f"credit channel unavailable: {error}")
        await asyncio.sleep(1.0)
        self._log(f"credits after subscribe: {self.credits} (seen={self.credit_seen})")

    async def write(self, payload: bytes) -> None:
        """Send ``payload`` in paced chunks, honouring ff03 credits if the printer grants any."""
        sent = 0
        for i in range(0, len(payload), self.chunk):
            if self.credit_seen:
                while self.credits <= 0:
                    self.credit_event.clear()
                    try:
                        await asyncio.wait_for(self.credit_event.wait(), 5)
                    except asyncio.TimeoutError:
                        raise IOError(f"no flow-control credit after {sent} packets") from None
                self.credits -= 1
            await self.client.write_gatt_char(CMD_CHAR, payload[i:i + self.chunk], response=False)
            sent += 1
            await asyncio.sleep(self.delay)
        self._log(f"sent {sent} packets ({len(payload)} bytes)")

    async def query(self, payload: bytes, length: int, timeout: float = 3.0) -> bytes:
        """Send a command and collect at least ``length`` reply bytes (or until timeout)."""
        while not self.replies.empty():
            self.replies.get_nowait()
        await self.write(payload)
        reply = b""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while len(reply) < length:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                reply += await asyncio.wait_for(self.replies.get(), remaining)
            except asyncio.TimeoutError:
                break
        return reply

    async def status(self) -> int | None:
        """Return the status byte (`ESC ! ?`), or None if the printer doesn't answer."""
        for cmd in (b"\x1b!?\r\n", b"\x1b!?"):
            reply = await self.query(cmd, 1)
            if reply:
                return reply[0]
        return None

    async def label(self) -> p21.Label:
        """Return the loaded roll as reported by its NFC tag (`ESC ! o`)."""
        for cmd in (b"\x1b!o\r\n", b"\x1b!o"):
            reply = await self.query(cmd, 16)
            if len(reply) >= 16:
                return p21.parse_label(reply[:16])
        raise IOError("no label block reply")

    async def wait_ready(self, timeout: float = 20.0) -> int | None:
        """Poll status until the printer reports READY or ``timeout`` passes."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        status = None
        while loop.time() < deadline:
            status = await self.status()
            if status == p21.Status.READY:
                return status
            await asyncio.sleep(1)
        return status


async def find_printer(name: str) -> BLEDevice:
    """Scan for an LE device advertising ``name``."""
    device = await BleakScanner.find_device_by_name(name, timeout=15)
    if device is None:
        sys.exit(f"No Bluetooth LE device named {name!r} found. Turn the printer on, close the "
                 "Nelko phone app (or turn the phone's Bluetooth off), make sure the printer isn't "
                 "connected in your computer's Bluetooth settings, and try again.")
    return device


def render_crisp(source, width: int, height: int, threshold: int = 140) -> bytes:
    """Convert an image to the printer's 1-bpp bitmap, tuned for text and line art.

    Landscape images are rotated to the head's portrait orientation, scaled to fit
    (Lanczos; a no-op when the image already matches), centred, and thresholded
    without dithering. Returns MSB-first rows of ``width // 8`` bytes, 0 = black.
    """
    img = Image.open(source).convert("L")
    if img.width > img.height:
        img = img.rotate(90, expand=True)
    scale = min(width / img.width, height / img.height)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    if size != img.size:
        img = img.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("L", (width, height), 255)
    canvas.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
    bitmap = canvas.point(lambda v: 255 if v >= threshold else 0).convert("1", dither=Image.Dither.NONE)
    return bitmap.tobytes()


def natural_key(text: str) -> list:
    """Sort key so L2 comes before L10."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def resolve_images(args: argparse.Namespace) -> list[Path]:
    """Turn label names / paths / --all into a list of image files."""
    if args.all:
        files = [f for f in args.labels_dir.glob("*.png") if f.stem.lower() != "preview"]
        return sorted(files, key=lambda f: natural_key(f.stem))
    paths = []
    for item in args.labels:
        path = Path(item)
        if not path.suffix:
            path = args.labels_dir / f"{item}.png"
        if not path.is_file():
            sys.exit(f"No such label image: {path}")
        paths.append(path)
    return paths


async def run(args: argparse.Namespace) -> None:
    """Connect once and perform the requested actions."""
    images = resolve_images(args)
    if args.all and not images:
        sys.exit(f"No PNG files in {args.labels_dir}")

    device = await find_printer(args.name)
    print(f"Found {device.name}; connecting...")
    async with BleakClient(device, timeout=20) as client:
        if args.gatt:
            for service in client.services:
                print(f"service {service.uuid}")
                for char in service.characteristics:
                    print(f"  {char.uuid}  handle {char.handle:#04x}  {','.join(char.properties)}")
            return

        printer = P21LE(client, debug=args.debug, chunk=args.chunk, delay=args.delay)
        await printer.start()

        if args.selftest:
            print("Printing self-test page...")
            await printer.write(b"SELFTEST\r\n")
            await asyncio.sleep(8)
            return

        status = await printer.status()
        print(f"Status: {'no reply' if status is None else p21.describe_status(status)}")
        if status is None:
            sys.exit("The printer did not answer over Bluetooth LE.")
        roll = await printer.label()
        print(f"Label: {roll}")
        if not images:
            return

        if args.label_size:
            width_mm, length_mm = (int(v) for v in args.label_size.lower().split("x"))
        elif roll.width_mm and roll.length_mm:
            width_mm, length_mm = roll.width_mm, roll.length_mm
        else:
            sys.exit("The roll has no readable size tag; pass --label-size WxL, e.g. --label-size 14x40.")

        for index, path in enumerate(images, 1):
            status = await printer.wait_ready()
            if status != p21.Status.READY:
                desc = "no reply" if status is None else p21.describe_status(status)
                sys.exit(f"Stopped before {path.name}: printer not ready ({desc}).")
            label = await printer.label()
            label.width_mm, label.length_mm = width_mm, length_mm
            width, height = p21.image_size(label, DPI)
            with Image.open(path) as probe:
                if sorted(probe.size) != sorted((width, height)):
                    print(f"  note: {path.name} is {probe.size[0]}x{probe.size[1]}, the label is "
                          f"{height}x{width} dots; it will be scaled to fit.")
            if args.render == "crisp":
                bitmap = render_crisp(path, width, height, args.threshold)
            else:
                bitmap = p21.render_image(str(path), width, height)
            job = p21.build_job(label, bitmap, width, height, args.density, args.copies)
            print(f"==> [{index}/{len(images)}] {path.stem} ({width_mm}x{length_mm} mm)")
            await printer.write(job)
            await asyncio.sleep(3)   # let it feed before polling again
        # Stay connected until the last job finishes, so disconnecting can't cut it short.
        status = await printer.wait_ready(timeout=30)
        print(f"Final status: {'no reply' if status is None else p21.describe_status(status)}")
        print(f"Done: {len(images)} label(s) sent.")


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(
        description="Print label images on a Nelko P21 over Bluetooth LE.",
        epilog="Labels are given by name (looked up as <labels-dir>/<name>.png) or as image paths.")
    parser.add_argument("labels", nargs="*", help="label names (e.g. L1 R3) or image files")
    parser.add_argument("--labels-dir", type=Path, default=Path("labels"),
                        help="folder for label names and --all (default: ./labels)")
    parser.add_argument("--all", action="store_true", help="print every PNG in --labels-dir, in natural order")
    parser.add_argument("--status", action="store_true", help="only show printer status and the loaded roll")
    parser.add_argument("--selftest", action="store_true", help="print the printer's built-in self-test page")
    parser.add_argument("--label-size", help="label size WxL in mm (default: read from the roll's tag)")
    parser.add_argument("--density", type=int, default=12, help="darkness 1-15 (default %(default)s)")
    parser.add_argument("--copies", type=int, default=1, help="copies of each label (default %(default)s)")
    parser.add_argument("--render", choices=["crisp", "dither"], default="crisp",
                        help="crisp = threshold, for text/line art (default); dither = for photos")
    parser.add_argument("--threshold", type=int, default=140,
                        help="crisp cut-off 0-255; higher = bolder (default %(default)s)")
    parser.add_argument("--name", default="P21", help="printer's advertised name (default %(default)s)")
    parser.add_argument("--chunk", type=int, default=CHUNK, help="bytes per LE write (default %(default)s)")
    parser.add_argument("--delay", type=float, default=CHUNK_DELAY, help="seconds between writes (default %(default)s)")
    parser.add_argument("--gatt", action="store_true", help="list the printer's LE services and exit")
    parser.add_argument("--debug", action="store_true", help="log MTU, credits and every reply")
    args = parser.parse_args()
    if args.status:
        args.labels, args.all = [], False
    if not (args.labels or args.all or args.status or args.selftest or args.gatt):
        parser.print_help()
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
