"""
TLS and STARTTLS handling.
"""
import asyncio
import copy
import ssl
from typing import Any, Callable, Coroutine, Optional, Type

import pytest
from aiosmtpd.smtp import SMTP as SMTPD

from aiosmtplib import (
    SMTP,
    SMTPConnectError,
    SMTPException,
    SMTPResponseException,
    SMTPServerDisconnected,
    SMTPStatus,
)


pytestmark = pytest.mark.asyncio()


async def test_tls_connection(
    tls_smtp_client: SMTP, tls_smtpd_server: asyncio.AbstractServer
) -> None:
    """
    Use an explicit connect/quit here, as other tests use the context manager.
    """
    await tls_smtp_client.connect()
    assert tls_smtp_client.is_connected

    await tls_smtp_client.quit()
    assert not tls_smtp_client.is_connected


async def test_starttls(
    smtp_client: SMTP, smtpd_server: asyncio.AbstractServer
) -> None:
    async with smtp_client:
        response = await smtp_client.starttls(validate_certs=False)

        assert response.code == SMTPStatus.ready

        # Make sure our state has been cleared
        assert not smtp_client.esmtp_extensions
        assert not smtp_client.supported_auth_methods
        assert not smtp_client.supports_esmtp

        # Make sure our connection was actually upgraded. ssl protocol transport is
        # private in UVloop, so just check the class name.
        assert "SSL" in type(smtp_client.transport).__name__

        response = await smtp_client.ehlo()
        assert response.code == SMTPStatus.completed


async def test_starttls_init_kwarg(hostname: str, smtpd_server_port: int) -> None:
    smtp_client = SMTP(
        hostname=hostname, port=smtpd_server_port, start_tls=True, validate_certs=False
    )

    async with smtp_client:
        # Make sure our connection was actually upgraded. ssl protocol transport is
        # private in UVloop, so just check the class name.
        assert "SSL" in type(smtp_client.transport).__name__


async def test_starttls_connect_kwarg(
    smtp_client: SMTP, smtpd_server: asyncio.AbstractServer
) -> None:
    await smtp_client.connect(start_tls=True, validate_certs=False)

    # Make sure our connection was actually upgraded. ssl protocol transport is
    # private in UVloop, so just check the class name.
    assert "SSL" in type(smtp_client.transport).__name__

    await smtp_client.quit()


async def test_starttls_with_explicit_server_hostname(
    smtp_client: SMTP, hostname: str, smtpd_server: asyncio.AbstractServer
) -> None:
    async with smtp_client:
        await smtp_client.ehlo()

        await smtp_client.starttls(validate_certs=False, server_hostname=hostname)


async def test_starttls_not_supported(
    smtp_client: SMTP,
    smtpd_server: asyncio.AbstractServer,
    smtpd_class: Type[SMTPD],
    smtpd_response_handler_factory: Callable[
        [Optional[str], Optional[str], bool, bool],
        Coroutine[Any, Any, None],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_handler = smtpd_response_handler_factory(
        f"{SMTPStatus.completed} HELP",
        None,
        False,
        False,
    )
    monkeypatch.setattr(smtpd_class, "smtp_EHLO", response_handler)

    async with smtp_client:
        await smtp_client.ehlo()

        with pytest.raises(SMTPException):
            await smtp_client.starttls(validate_certs=False)


async def test_starttls_advertised_but_not_supported(
    smtp_client: SMTP,
    smtpd_server: asyncio.AbstractServer,
    smtpd_class: Type[SMTPD],
    smtpd_response_handler_factory: Callable[
        [Optional[str], Optional[str], bool, bool],
        Coroutine[Any, Any, None],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_handler = smtpd_response_handler_factory(
        f"{SMTPStatus.tls_not_available} please login", None, False, False
    )
    monkeypatch.setattr(smtpd_class, "smtp_STARTTLS", response_handler)

    async with smtp_client:
        await smtp_client.ehlo()

        with pytest.raises(SMTPException):
            await smtp_client.starttls(validate_certs=False)


async def test_starttls_disconnect_before_upgrade(
    smtp_client: SMTP,
    smtpd_server: asyncio.AbstractServer,
    smtpd_class: Type[SMTPD],
    smtpd_response_handler_factory: Callable[
        [Optional[str], Optional[str], bool, bool],
        Coroutine[Any, Any, None],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_handler = smtpd_response_handler_factory(
        f"{SMTPStatus.ready} Go for it", None, False, True
    )
    monkeypatch.setattr(smtpd_class, "smtp_STARTTLS", response_handler)

    async with smtp_client:
        with pytest.raises(SMTPServerDisconnected):
            await smtp_client.starttls(validate_certs=False)


async def test_starttls_invalid_responses(
    smtp_client: SMTP,
    smtpd_server: asyncio.AbstractServer,
    event_loop: asyncio.AbstractEventLoop,
    smtpd_class: Type[SMTPD],
    smtpd_response_handler_factory: Callable[
        [Optional[str], Optional[str], bool, bool],
        Coroutine[Any, Any, None],
    ],
    monkeypatch: pytest.MonkeyPatch,
    error_code: int,
) -> None:
    response_handler = smtpd_response_handler_factory(
        f"{error_code} error",
        None,
        False,
        False,
    )
    monkeypatch.setattr(smtpd_class, "smtp_STARTTLS", response_handler)

    async with smtp_client:
        await smtp_client.ehlo()

        old_extensions = copy.copy(smtp_client.esmtp_extensions)

        with pytest.raises(SMTPResponseException) as exception_info:
            await smtp_client.starttls(validate_certs=False)

        assert exception_info.value.code == error_code
        # Make sure our state has been _not_ been cleared
        assert smtp_client.esmtp_extensions == old_extensions
        assert smtp_client.supports_esmtp is True

        # Make sure our connection was not upgraded. ssl protocol transport is
        # private in UVloop, so just check the class name.
        assert "SSL" not in type(smtp_client.transport).__name__


async def test_starttls_with_client_cert(
    smtp_client: SMTP,
    smtpd_server: asyncio.AbstractServer,
    valid_cert_path: str,
    valid_key_path: str,
) -> None:
    async with smtp_client:
        response = await smtp_client.starttls(
            client_cert=valid_cert_path,
            client_key=valid_key_path,
            cert_bundle=valid_cert_path,
            validate_certs=True,
        )

        assert response.code == SMTPStatus.ready
        assert smtp_client.client_cert == valid_cert_path
        assert smtp_client.client_key == valid_key_path
        assert smtp_client.cert_bundle == valid_cert_path


async def test_starttls_with_invalid_client_cert(
    smtp_client: SMTP,
    smtpd_server: asyncio.AbstractServer,
    invalid_cert_path: str,
    invalid_key_path: str,
) -> None:
    async with smtp_client:
        with pytest.raises(ssl.SSLError):
            await smtp_client.starttls(
                client_cert=invalid_cert_path,
                client_key=invalid_key_path,
                cert_bundle=invalid_cert_path,
                validate_certs=True,
            )


async def test_starttls_cert_error(
    event_loop: asyncio.AbstractEventLoop,
    smtp_client: SMTP,
    smtpd_server: asyncio.AbstractServer,
) -> None:
    # Don't fail on the expected exception
    event_loop.set_exception_handler(None)

    async with smtp_client:
        with pytest.raises(ssl.SSLError):
            await smtp_client.starttls(validate_certs=True)


async def test_tls_get_transport_info(
    tls_smtp_client: SMTP,
    hostname: str,
    tls_smtpd_server_port: int,
    event_loop: asyncio.AbstractEventLoop,
) -> None:
    async with tls_smtp_client:
        compression = tls_smtp_client.get_transport_info("compression")
        assert compression is None  # Compression is not used here

        peername = tls_smtp_client.get_transport_info("peername")
        assert peername[0] in ("127.0.0.1", "::1")  # IP v4 and 6
        assert peername[1] == tls_smtpd_server_port

        sock = tls_smtp_client.get_transport_info("socket")
        assert sock is not None

        sockname = tls_smtp_client.get_transport_info("sockname")
        assert sockname is not None

        cipher = tls_smtp_client.get_transport_info("cipher")
        assert cipher is not None

        peercert = tls_smtp_client.get_transport_info("peercert")
        assert peercert is not None

        sslcontext = tls_smtp_client.get_transport_info("sslcontext")
        assert sslcontext is not None

        sslobj = tls_smtp_client.get_transport_info("ssl_object")
        assert sslobj is not None


async def test_tls_smtp_connect_to_non_tls_server(
    event_loop: asyncio.AbstractEventLoop,
    tls_smtp_client: SMTP,
    smtpd_server_port: int,
) -> None:
    # Don't fail on the expected exception
    event_loop.set_exception_handler(None)

    with pytest.raises(SMTPConnectError):
        await tls_smtp_client.connect(port=smtpd_server_port)
    assert not tls_smtp_client.is_connected


async def test_tls_connection_with_existing_sslcontext(
    tls_smtp_client: SMTP,
    tls_smtpd_server: asyncio.AbstractServer,
    client_tls_context: ssl.SSLContext,
) -> None:
    await tls_smtp_client.connect(tls_context=client_tls_context)
    assert tls_smtp_client.is_connected

    assert tls_smtp_client.tls_context is client_tls_context

    await tls_smtp_client.quit()
    assert not tls_smtp_client.is_connected


async def test_tls_connection_with_client_cert(
    tls_smtp_client: SMTP,
    tls_smtpd_server: asyncio.AbstractServer,
    hostname: str,
    valid_cert_path: str,
    valid_key_path: str,
) -> None:
    await tls_smtp_client.connect(
        hostname=hostname,
        validate_certs=True,
        client_cert=valid_cert_path,
        client_key=valid_key_path,
        cert_bundle=valid_cert_path,
    )
    assert tls_smtp_client.is_connected

    await tls_smtp_client.quit()
    assert not tls_smtp_client.is_connected


async def test_tls_connection_with_cert_error(
    event_loop: asyncio.AbstractEventLoop,
    tls_smtp_client: SMTP,
    tls_smtpd_server: asyncio.AbstractServer,
) -> None:
    # Don't fail on the expected exception
    event_loop.set_exception_handler(None)

    with pytest.raises(SMTPConnectError) as exception_info:
        await tls_smtp_client.connect(validate_certs=True)

    assert "CERTIFICATE_VERIFY_FAILED" in str(exception_info.value)
