"""Tests for Celery configuration and utilities."""

from sqlalchemy.exc import InterfaceError, OperationalError

from potluck.core.celery import is_fatal_error, is_transient_error


class TestIsTransientError:
    """Tests for is_transient_error helper."""

    def test_operational_error_is_transient(self) -> None:
        """OperationalError (DB connection issue) is transient."""
        error = OperationalError("statement", {}, Exception())
        assert is_transient_error(error) is True

    def test_interface_error_is_transient(self) -> None:
        """InterfaceError (DB interface issue) is transient."""
        error = InterfaceError("statement", {}, Exception())
        assert is_transient_error(error) is True

    def test_disk_io_error_is_transient(self) -> None:
        """OSError with EIO (5) is transient."""
        error = OSError(5, "Input/output error")
        assert is_transient_error(error) is True

    def test_disk_full_error_is_transient(self) -> None:
        """OSError with ENOSPC (28) is transient."""
        error = OSError(28, "No space left on device")
        assert is_transient_error(error) is True

    def test_readonly_fs_error_is_transient(self) -> None:
        """OSError with EROFS (30) is transient."""
        error = OSError(30, "Read-only file system")
        assert is_transient_error(error) is True

    def test_other_os_error_not_transient(self) -> None:
        """OSError with other errno is not transient."""
        error = OSError(2, "No such file or directory")
        assert is_transient_error(error) is False

    def test_value_error_not_transient(self) -> None:
        """ValueError is not transient."""
        error = ValueError("bad value")
        assert is_transient_error(error) is False

    def test_runtime_error_not_transient(self) -> None:
        """RuntimeError is not transient."""
        error = RuntimeError("runtime issue")
        assert is_transient_error(error) is False


class TestIsFatalError:
    """Tests for is_fatal_error helper."""

    def test_file_not_found_is_fatal(self) -> None:
        """FileNotFoundError is fatal."""
        error = FileNotFoundError("File not found")
        assert is_fatal_error(error) is True

    def test_permission_error_is_fatal(self) -> None:
        """PermissionError is fatal."""
        error = PermissionError("Access denied")
        assert is_fatal_error(error) is True

    def test_value_error_not_fatal(self) -> None:
        """ValueError is not fatal."""
        error = ValueError("bad value")
        assert is_fatal_error(error) is False

    def test_os_error_not_fatal(self) -> None:
        """Generic OSError is not fatal."""
        error = OSError(5, "Input/output error")
        assert is_fatal_error(error) is False

    def test_operational_error_not_fatal(self) -> None:
        """OperationalError is not fatal (should retry)."""
        error = OperationalError("statement", {}, Exception())
        assert is_fatal_error(error) is False
