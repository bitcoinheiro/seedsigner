from pyzbar import pyzbar
from pyzbar.pyzbar import ZBarSymbol
import re
from enum import IntEnum
import base64
from embit import bip39, psbt
from binascii import a2b_base64, b2a_base64
from seedsigner.helpers.ur2.ur_decoder import URDecoder
from seedsigner.helpers.bcur import (cbor_decode, bc32decode)

###
### DecodeQR Class
### Purpose: used to process images or string data from animated qr codes with psbt data to create binary/base64 psbt
###

class DecodeQR:

    def __init__(self, qr_type=None):
        self.complete = False
        self.qr_type = qr_type
        self.ur_decoder = URDecoder() # UR2 decoder
        self.specter_qr = SpecterDecodePSBTQR() # Specter Desktop PSBT QR base64 decoder
        self.legacy_ur = LegacyURDecodeQR() # UR Legacy decoder
        self.base64_qr = Base64DecodeQR() # Single Segments Base64
        self.seedqr = SeedQR()

    def addImage(self, image):
        qr_str = DecodeQR.QR2Str(image)
        if not qr_str:
            return DecodeQRStatus.FALSE

        return self.addString(qr_str)

    def addString(self, qr_str):
        qr_type = DecodeQR.SegmentType(qr_str)

        if self.qr_type == None:
            self.qr_type = qr_type
        elif self.qr_type != qr_type:
            raise Exception('QR Fragement Unexpected Type Change')

        if self.qr_type == QRType.PSBTUR2:

            self.ur_decoder.receive_part(qr_str)
            if self.ur_decoder.is_complete():
                self.complete = True
                return DecodeQRStatus.COMPLETE
            return DecodeQRStatus.PART_COMPLETE # segment added to ur2 decoder

        elif self.qr_type == QRType.PSBTSPECTER:

            rt = self.specter_qr.add(qr_str)
            if rt == DecodeQRStatus.COMPLETE:
                self.complete = True
            return rt

        elif self.qr_type == QRType.PSBTURLEGACY:

            rt = self.legacy_ur.add(qr_str)
            if rt == DecodeQRStatus.COMPLETE:
                self.complete = True
            return rt

        elif self.qr_type == QRType.PSBTBASE64:

            rt = self.base64_qr.add(qr_str)
            if rt == DecodeQRStatus.COMPLETE:
                self.complete = True
            return rt

        elif self.qr_type == QRType.SEEDSSQR:

            rt = self.seedqr.add(qr_str)
            if rt == DecodeQRStatus.COMPLETE:
                self.complete
            return rt

        else:
            return DecodeQRStatus.INVALID

    def getPSBT(self):
        if self.complete:
            try:
                if self.qr_type == QRType.PSBTUR2:
                    cbor = self.ur_decoder.result_message().cbor
                    return psbt.PSBT.parse(cbor_decode(cbor))
                elif self.qr_type == QRType.PSBTSPECTER:
                    return psbt.PSBT.parse(self.specter_qr.getData())
                elif self.qr_type == QRType.PSBTURLEGACY:
                    return psbt.PSBT.parse(self.legacy_ur.getData())
                elif self.qr_type == QRType.PSBTBASE64:
                    return psbt.PSBT.parse(self.base64_qr.getData())
            except:
                return None
        return None

    def getBase64PSBT(self):
        if self.complete:
            data = self.getData()
            b64_psbt = b2a_base64(data)

            if b64_psbt[-1:] == b"\n":
                b64_psbt = b64_psbt[:-1]

            return b64_psbt.decode("utf-8")
        return None

    def getSeedPhrase(self):
        self.seedqr.getSeedPhrase()

    def getPercentComplete(self) -> int:
        if self.qr_type == QRType.PSBTUR2:
            return int(self.ur_decoder.estimated_percent_complete() * 100)
        elif self.qr_type == QRType.PSBTSPECTER:
            if self.specter_qr.total_segments == None:
                return 0
            return int((self.specter_qr.collected_segments / self.specter_qr.total_segments) * 100)
        elif self.qr_type == QRType.PSBTURLEGACY:
            if self.legacy_ur.total_segments == None:
                return 0
            return int((self.legacy_ur.collected_segments / self.legacy_ur.total_segments) * 100)
        elif self.qr_type == QRType.PSBTBASE64:
            if self.base64_qr.complete:
                return 100
            else:
                return 0
        else:
            return 0

    def isComplete(self) -> bool:
        return self.complete

    def isPSBT(self) -> bool:
        if self.qr_type in (QRType.PSBTUR2, QRType.PSBTSPECTER, QRType.PSBTURLEGACY, QRType.PSBTBASE64):
            return True
        return False

    def isSeed(self):
        if self.qr_type in (QRType.SEEDSSQR, QRType.SEEDUR2):
            return True
        return False

    def qrType(self):
        return self.qr_type

    @staticmethod
    def QR2Str(image) -> str:

        barcodes = pyzbar.decode(frame, symbols=[ZBarSymbol.QRCODE])

        for barcode in barcodes:
            # Only pull and return the first barcode
            return barcode.data.decode("utf-8")

    @staticmethod
    def SegmentType(s):

        if re.search("^UR:CRYPTO-PSBT/", s, re.IGNORECASE):
            return QRType.PSBTUR2
        elif re.search(r'^p(\d+)of(\d+) ', s, re.IGNORECASE):
            return QRType.PSBTSPECTER
        elif re.search("^UR:BYTES/", s, re.IGNORECASE):
            return QRType.PSBTURLEGACY
        elif re.search(r'\d{48,96}', s):
            return QRType.SEEDSSQR
        elif DecodePSBTQR.isBase64PSBT(s):
            return QRType.PSBTBASE64
        else:
            return QRType.INVALID

    @staticmethod   
    def isBase64(s):
        try:
            return base64.b64encode(base64.b64decode(s)) == s.encode('ascii')
        except Exception:
            return False

    @staticmethod   
    def isBase64PSBT(s):
        try:
            if DecodeQR.isBase64(s):
                psbt.PSBT.parse(a2b_base64(s))
                return True
        except Exception:
            return False
        return False

###
### SpecterDecodePSBTQR Class
### Purpose: used in DecodePSBTQR to decode Specter Desktop Animated QR PSBT encoding
###

class SpecterDecodePSBTQR:

    def __init__(self):
        self.total_segments = None
        self.collected_segments = 0
        self.complete = False
        self.segments = []

    def add(self, segment):
        if self.total_segments == None:
            self.total_segments = SpecterDecodePSBTQR.totalSegmentNum(segment)
            self.segments = [None] * self.total_segments
        elif self.total_segments != SpecterDecodePSBTQR.totalSegmentNum(segment):
            raise Exception('Specter Desktop segment total changed unexpectedly')

        if self.segments[SpecterDecodePSBTQR.currentSegmentNum(segment) - 1] == None:
            self.segments[SpecterDecodePSBTQR.currentSegmentNum(segment) - 1] = SpecterDecodePSBTQR.parseSegment(segment)
            self.collected_segments += 1
            if self.total_segments == self.collected_segments:
                self.complete = True
                return DecodeQRStatus.COMPLETE
            return DecodeQRStatus.PART_COMPLETE # new segment added

        return DecodeQRStatus.PART_EXISTING # segment not added because it's already been added

    def getBase64Data(self) -> str:
        base64 = "".join(self.segments)
        if self.complete and DecodeQR.isBase64(base64):
            return base64

        return None

    def getData(self):
        base64 = self.getBase64Data()
        if base64 != None:
            return a2b_base64(base64)

        return None

    def is_complete(self) -> bool:
        return self.complete

    @staticmethod
    def currentSegmentNum(segment) -> int:
        if DecodeQR.SegmentType(segment) == QRType.PSBTSPECTER:
            if re.search(r'^p(\d+)of(\d+) ', segment, re.IGNORECASE) != None:
                return int(re.search(r'^p(\d+)of(\d+) ', segment, re.IGNORECASE).group(1))
        raise Exception('Unable to parse Specter Desktop segment')

    @staticmethod
    def totalSegmentNum(segment) -> int:
        if DecodeQR.SegmentType(segment) == QRType.PSBTSPECTER:
            if re.search(r'^p(\d+)of(\d+) ', segment, re.IGNORECASE) != None:
                return int(re.search(r'^p(\d+)of(\d+) ', segment, re.IGNORECASE).group(2))
        raise Exception('Unable to parse Specter Desktop segment')

    @staticmethod
    def parseSegment(segment) -> str:
        return segment.split(" ")[-1].strip()

###
### LegacyURDecodeQR Class
### Purpose: used in DecodeQR to decode Legacy UR animated qr encoding
###

class LegacyURDecodeQR:

    def __init__(self):
        self.total_segments = None
        self.collected_segments = 0
        self.complete = False
        self.segments = []

    def add(self, segment):
        if self.total_segments == None:
            self.total_segments = LegacyURDecodeQR.totalSegmentNum(segment)
            self.segments = [None] * self.total_segments
        elif self.total_segments != LegacyURDecodeQR.totalSegmentNum(segment):
            raise Exception('UR Legacy segment total changed unexpectedly')

        if self.segments[LegacyURDecodeQR.currentSegmentNum(segment) - 1] == None:
            self.segments[LegacyURDecodeQR.currentSegmentNum(segment) - 1] = LegacyURDecodeQR.parseSegment(segment)
            self.collected_segments += 1
            if self.total_segments == self.collected_segments:
                self.complete = True
                return DecodeQRStatus.COMPLETE
            return DecodeQRStatus.PART_COMPLETE # new segment added

        return DecodeQRStatus.PART_EXISTING # segment not added because it's already been added

    def getBase64Data(self) -> str:
        bc32_cbor = "".join(self.segments)
        raw = cbor_decode(bc32decode(bc32_cbor))
        base64 = b2a_base64(raw)

        if self.complete:
            return base64

        return None

    def getData(self):
        if not self.complete:
            return None

        bc32_cbor = "".join(self.segments)
        raw = cbor_decode(bc32decode(bc32_cbor))
        return raw

    def is_complete(self) -> bool:
        return self.complete

    @staticmethod
    def currentSegmentNum(segment) -> int:
        if DecodeQR.SegmentType(segment) == QRType.PSBTURLEGACY:
            if re.search(r'^UR:BYTES/(\d+)OF(\d+)', segment, re.IGNORECASE) != None:
                return int(re.search(r'^UR:BYTES/(\d+)OF(\d+)', segment, re.IGNORECASE).group(1))
            else:
                raise Exception('Unexpected Legacy UR Error')
        raise Exception('Unable to parse Legacy UR segment')

    @staticmethod
    def totalSegmentNum(segment) -> int:
        if DecodeQR.SegmentType(segment) == QRType.PSBTURLEGACY:
            if re.search(r'^UR:BYTES/(\d+)OF(\d+)', segment, re.IGNORECASE) != None:
                return int(re.search(r'^UR:BYTES/(\d+)OF(\d+)', segment, re.IGNORECASE).group(2))
            else:
                return 1
        raise Exception('Unable to parse Legacy UR segment')

    @staticmethod
    def parseSegment(segment) -> str:
        return segment.split("/")[-1].strip()

###
### Base64DecodeQR Class
### Purpose: used in DecodeQR to decode single frame base64 encoded qr image
###          does not support animated qr because no idicator or segments or thier order
###

class Base64DecodeQR:

    def __init__(self):
        self.total_segments = 1
        self.collected_segments = 0
        self.complete = False
        self.data = None

    def add(self, segment):
        if DecodeQR.isBase64(segment):
            self.complete = True
            self.data = segment
            self.collected_segments = 1
            return DecodeQRStatus.COMPLETE

        return DecodeQRStatus.INVALID

    def getBase64Data(self) -> str:
        return self.data

    def getData(self):
        base64 = self.getBase64Data()
        if base64 != None:
            return a2b_base64(base64)

        return None

    @staticmethod
    def currentSegmentNum(segment) -> int:
        return self.collected_segments

    @staticmethod
    def totalSegmentNum(segment) -> int:
        return self.total_segments

    @staticmethod
    def parseSegment(segment) -> str:
        return segment

class SeedQR:

    def __init__(self):
        self.total_segments = 1
        self.collected_segments = 0
        self.complete = False
        self.seed_phrase = []

    def add(self, segment):
        try:
            self.seed_phrase = []

            # Parse 12 or 24-word QR code
            num_words = int(len(segment[0]) / 4)
            for i in range(0, num_words):
                index = int(segment[0][i * 4: (i*4) + 4])
                word = bip39.WORDLIST[index]
                self.seed_phrase.append(word)
            return DecodeQRStatus.COMPLETE
        except Exception as e:
            return DecodeQRStatus.INVALID

    def getSeedPhrase(self):
        if len(self.seed_phrase) > 0 and self.complete:
            return self.seed_phrase
        return None


###
### QRType Class IntEum
### Purpose: used in DecodeQR to communicate qr encoding type
###

class QRType(IntEnum):
    PSBTBASE64 = 1
    PSBTSPECTER = 2
    PSBTURLEGACY = 3
    PSBTUR2 = 5
    SEEDSSQR = 6
    SEEDUR2 = 7
    INVALID = 100

###
### DecodeQRStatus Class IntEum
### Purpose: used in DecodeQR to communicate status of adding qr frame/segment
###

class DecodeQRStatus(IntEnum):
    PART_COMPLETE = 1
    PART_EXISTING = 2
    COMPLETE = 3
    FALSE = 4
    INVALID = 5