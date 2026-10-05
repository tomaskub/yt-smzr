"""Keyboard-accessible nonsecret settings and explicit installed-model refresh."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from yt_smzr.config import ENV_FIELDS, ConfigurationError, Settings, save_settings
from yt_smzr.summarization.base import SummarizationError
from yt_smzr.summarization.ollama_provider import list_models

ModelLoader = Callable[[Settings], tuple[str, ...]]


class SettingsScreen(ModalScreen[Settings | None]):
    """Apply affects this session; Save also persists nonsecret selections."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CSS = """
    SettingsScreen { align: center middle; background: $background; }
    SettingsScreen .dialog { width: 72; max-width: 100%; height: auto;
        max-height: 100%; border: solid $primary; padding: 0 1; }
    SettingsScreen Input, SettingsScreen Select { height: 3; }
    SettingsScreen Static { height: auto; }
    SettingsScreen Horizontal { height: auto; }
    SettingsScreen Button { min-width: 10; margin-right: 1; }
    """

    def __init__(
        self, settings: Settings, *, model_loader: ModelLoader = list_models
    ) -> None:
        super().__init__()
        self.settings = settings
        self.model_loader = model_loader
        self._listing = False

    def compose(self) -> ComposeResult:
        import os

        with VerticalScroll(classes="dialog"):
            yield Static("Summarization settings", markup=False)
            yield Select(
                [(name, name) for name in ("openai", "openrouter", "ollama")],
                value=self.settings.summarization_provider,
                allow_blank=False,
                id="settings-provider",
            )
            yield Static("Model, explicit installed tag for Ollama", markup=False)
            yield Input(
                value=self.settings.summarization_model or "", id="settings-model"
            )
            yield Static("Ollama HTTP/HTTPS server", markup=False)
            yield Input(value=self.settings.ollama_base_url, id="settings-server")
            yield Static("Request timeout in seconds", markup=False)
            yield Input(
                value=str(self.settings.ollama_timeout_seconds), id="settings-timeout"
            )
            yield Button("Refresh models", id="settings-refresh")
            yield Static(
                "Refresh lists installed models; manual entry is allowed.",
                id="settings-models",
                markup=False,
            )
            overrides = [name for name in ENV_FIELDS.values() if name in os.environ]
            notice = (
                "Environment overrides after restart: " + ", ".join(overrides)
                if overrides
                else "No active environment overrides."
            )
            yield Static(notice, id="settings-overrides", markup=False)
            yield Static(
                "Apply changes subsequent runs. Save also writes ~/.yt-smzr.toml.",
                id="settings-notice",
                markup=False,
            )
            with Horizontal():
                yield Button("Apply", id="settings-apply", variant="primary")
                yield Button("Save", id="settings-save")
                yield Button("Cancel", id="settings-cancel")

    def on_mount(self) -> None:
        self.query_one("#settings-provider", Select).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selection(self, *, require_model: bool = True) -> Settings:
        provider = cast(Select[str], self.query_one("#settings-provider", Select)).value
        model = self.query_one("#settings-model", Input).value.strip()
        if not isinstance(provider, str) or provider not in {
            "openai",
            "openrouter",
            "ollama",
        }:
            raise ConfigurationError("Choose a supported summarization provider.")
        if require_model and not model:
            raise ConfigurationError(
                "Enter an explicit model name before applying settings."
            )
        try:
            timeout = float(self.query_one("#settings-timeout", Input).value)
        except ValueError:
            raise ConfigurationError(
                "ollama.timeout_seconds must be finite and positive."
            ) from None
        selected = replace(
            self.settings,
            summarization_provider=provider,
            summarization_model=model,
            ollama_base_url=self.query_one("#settings-server", Input).value.strip(),
            ollama_timeout_seconds=timeout,
        )
        from yt_smzr.config import validate_ollama_settings

        validate_ollama_settings(selected)
        return selected

    @on(Button.Pressed, "#settings-apply")
    @on(Button.Pressed, "#settings-save")
    def apply_selection(self, event: Button.Pressed) -> None:
        try:
            selection = self._selection()
            if event.button.id == "settings-save":
                save_settings(selection)
        except ConfigurationError as exc:
            self.query_one("#settings-notice", Static).update(str(exc))
        else:
            self.dismiss(selection)

    @on(Button.Pressed, "#settings-cancel")
    def cancel_clicked(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#settings-refresh")
    def refresh_requested(self) -> None:
        if self._listing:
            return
        try:
            selection = self._selection(require_model=False)
        except ConfigurationError as exc:
            self.query_one("#settings-models", Static).update(str(exc))
            return
        self._listing = True
        self.query_one("#settings-refresh", Button).disabled = True
        self.query_one("#settings-models", Static).update(
            "Checking installed models..."
        )
        self._refresh_models(selection)

    @work(thread=True, exit_on_error=False)
    def _refresh_models(self, settings: Settings) -> None:
        try:
            models = self.model_loader(settings)
            message = (
                "Installed models: " + ", ".join(models)
                if models
                else "No installed models. Install a compatible model with ollama pull."
            )
        except (SummarizationError, ConfigurationError) as exc:
            message = str(exc)
        except Exception:
            message = "Could not list Ollama models. Check the server and connectivity."
        try:
            cast(App[None], cast(Any, self).app).call_from_thread(
                self._models_ready, message
            )
        except RuntimeError:
            pass

    def _models_ready(self, message: str) -> None:
        if not self.is_mounted:
            return
        self._listing = False
        self.query_one("#settings-refresh", Button).disabled = False
        self.query_one("#settings-models", Static).update(message)
