import secrets
from abc import (
    ABC,
    abstractmethod,
)
from typing import Protocol, runtime_checkable
from Crypto.Cipher import AES
from jiuwenclaw.utils import logger


class BaseCrypto(ABC):
    _instance = None  # 单例缓存
    _other_classes = []  # 按定义顺序存储子类
    _default_subclass = None  # 标记哪个是 DefaultCrypto

    def __init_subclass__(cls, **kwargs):
        # 如果子类名是 'DefaultCrypto'，则标记为默认子类
        if cls.__name__ == "DefaultCrypto":
            BaseCrypto._default_subclass = cls
        else:
            BaseCrypto._other_classes.append(cls)
        logger.info(f"Registered crypto class: {cls.__name__}")

    @staticmethod
    def get_instance():
        """获取全局唯一实例，优先使用非 DefaultCrypto 的子类"""
        if BaseCrypto._instance is None:
            # 寻找第一个非 DefaultCrypto 的子类
            selected_cls = BaseCrypto._default_subclass
            if BaseCrypto._other_classes:
                selected_cls = BaseCrypto._other_classes[0]

            logger.info(f"创建实例，使用的子类: {selected_cls.__name__}")
            BaseCrypto._instance = selected_cls()
        return BaseCrypto._instance

    @abstractmethod
    def encrypt(self, plaintext: str, **kwargs) -> str:
        pass

    @abstractmethod
    def decrypt(self, ciphertext: str, **kwargs) -> str:
        pass


NONCE_LENGTH = 12
BIT_LENGTH = 8
AES_KEY_LENGTH = 32
TAG_LENGTH = 16
NONCE_HEX_LENGTH = NONCE_LENGTH * 2  # hex_length = bytes_length * 2
TAG_HEX_LENGTH = TAG_LENGTH * 2  # hex_length = bytes_length * 2
DEFAULT_KEY = b'00000000000000000000000000000000'


def _encrypt(key: bytes, plaintext: str):
    if len(key) != AES_KEY_LENGTH:
        raise ValueError(f'Wrong key length: {len(key)}, expected {AES_KEY_LENGTH}')
    random_instance = secrets.SystemRandom()
    nonce = bytes([random_instance.getrandbits(BIT_LENGTH) for _ in range(0, NONCE_LENGTH)])
    cipher = AES.new(key=key, mode=AES.MODE_GCM, nonce=nonce, mac_len=TAG_LENGTH)
    cipher_text, tag = cipher.encrypt_and_digest(plaintext.encode(encoding="utf-8"))
    return [cipher_text.hex(), nonce.hex(), tag.hex()]


def _decrypt(key: bytes, ciphertext: str, nonce: str, tag: str):
    ciphertext_bytes = bytes.fromhex(ciphertext)
    nonce_bytes = bytes.fromhex(nonce)
    tag_bytes = bytes.fromhex(tag)
    if len(key) != AES_KEY_LENGTH:
        raise ValueError(f'Wrong key length: {len(key)}, expected {AES_KEY_LENGTH}')

    if len(nonce_bytes) != NONCE_LENGTH:
        raise ValueError(f"Wrong nonce length: {len(nonce_bytes)}")

    if len(tag_bytes) != TAG_LENGTH:
        raise ValueError(f"Wrong tag length: {len(tag_bytes)}, expected {TAG_LENGTH}")

    cipher = AES.new(key=key, mode=AES.MODE_GCM, nonce=nonce_bytes)
    plaintext_bytes = cipher.decrypt_and_verify(ciphertext=ciphertext_bytes, received_mac_tag=tag_bytes)
    return plaintext_bytes.decode(encoding="utf-8")


def encrypt(plaintext: str, **kwargs) -> str:
    key = kwargs.get('key')
    if key is not None and not isinstance(key, bytes):
        key = str(key).encode('utf-8')
    if not key:
        key = DEFAULT_KEY
    if not plaintext:
        return plaintext
    try:
        encrypt_memory, nonce, tag = _encrypt(key=key, plaintext=plaintext)
        return f"{nonce}{tag}{encrypt_memory}"
    except Exception as e:
        logger.warning("Encrypt error occurred: %s", str(e))
        return plaintext


def decrypt(ciphertext: str, **kwargs) -> str:
    key = kwargs.get('key')
    if key is not None and not isinstance(key, bytes):
        key = str(key).encode('utf-8')
    if not key:
        key = DEFAULT_KEY
    if not ciphertext:
        return ciphertext
    nonce_and_tag_len = NONCE_HEX_LENGTH + TAG_HEX_LENGTH
    if len(ciphertext) < nonce_and_tag_len:
        logger.warning(
            "Decryption error occurred: invalid ciphertext, ciphertext_len = %s", len(ciphertext)
        )
        return ciphertext

    nonce = ciphertext[0:NONCE_HEX_LENGTH]
    tag = ciphertext[NONCE_HEX_LENGTH:nonce_and_tag_len]
    encrypt_memory = ciphertext[nonce_and_tag_len:]
    try:
        return _decrypt(key=key, ciphertext=encrypt_memory, nonce=nonce, tag=tag)
    except Exception as e:
        logger.warning("Decrypt error occurred: %s", str(e))
        return ciphertext


class DefaultCrypto(BaseCrypto):
    def encrypt(self, plaintext: str, **kwargs) -> str:
        return encrypt(plaintext, **kwargs)

    def decrypt(self, ciphertext: str, **kwargs) -> str:
        return decrypt(ciphertext, **kwargs)


@runtime_checkable
class CryptoProvider(Protocol):
    def encrypt(self, plaintext: str, **kwargs) -> str:
        pass

    def decrypt(self, ciphertext: str, **kwargs) -> str:
        pass

_default_provider: CryptoProvider = DefaultCrypto()


def set_crypto_provider(provider: CryptoProvider) -> None:
    global _default_provider
    _default_provider = provider


def get_crypto_provider() -> CryptoProvider:
    return _default_provider