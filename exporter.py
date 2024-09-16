#!/usr/bin/env python3

import asyncio
import os
import shutil
import tempfile
from urllib.parse import urlparse
from collections import defaultdict

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Header, Footer, Input, Static, Label, SelectionList
from textual.widgets.selection_list import Selection
from textual.reactive import reactive
from textual.timer import Timer


class GitHubExporter(App):
    """Main application class for GitHub file exporter."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "fetch", "Fetch Files"),
        ("e", "export", "Export"),
        ("c", "cancel_exit", "Cancel Exit"),
    ]

    CSS = """
    #url_input, #export_name { width: 1fr; }
    #file_list { height: 1fr; border: solid $accent; margin: 1; }
    #loading { align: center middle; }
    #error_message { color: red; text-align: center; }
    #export_info { height: auto; }
    Button { width: 20; }
    .input-row { height: auto; margin: 1; }
    .input-row > Horizontal { height: 3; align: left middle; }
    SelectionList { border: none; }
    """

    file_selections = reactive([])

    def __init__(self):
        super().__init__()
        self.temp_dir = None
        self.repo_name = ""
        self.cli_url = ""
        self.exit_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container():
            with Horizontal(classes="input-row"):
                with Horizontal():
                    yield Input(id="url_input", placeholder="Enter GitHub URL")
                    yield Button("Fetch Files", id="fetch_button", variant="primary")
            with Horizontal(classes="input-row"):
                with Horizontal():
                    yield Input(id="export_name", placeholder="Export file name")
                    yield Button("Export", id="export_button", variant="success")
            yield SelectionList(id="file_list")
            yield Static("", id="loading")
            yield Label("", id="error_message")
            yield Static("", id="export_info")
        yield Footer()

    def on_mount(self):
        self.query_one("#loading").update("Loading...")
        self.query_one("#loading").styles.display = "none"
        url_input = self.query_one("#url_input")
        if self.cli_url:
            url_input.value = self.cli_url
            self.action_fetch()
        else:
            url_input.focus()

    def watch_file_selections(self, file_selections):
        file_list = self.query_one("#file_list")
        file_list.clear_options()
        for selection in file_selections:
            file_list.add_option(selection)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "fetch_button":
            self.action_fetch()
        elif event.button.id == "export_button":
            self.action_export()

    @work
    async def action_fetch(self):
        url = self.query_one("#url_input").value
        if not url:
            self.show_error("Please enter a GitHub URL")
            return

        self.show_loading(True)
        self.show_error("")

        try:
            parsed_url = urlparse(url)
            _, user, repo, *_ = parsed_url.path.split("/")
            self.repo_name = repo
            self.temp_dir = tempfile.mkdtemp()

            clone_command = f"git clone --depth 1 {url} {self.temp_dir}"
            process = await asyncio.create_subprocess_shell(
                clone_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise Exception(f"Git clone failed: {stderr.decode()}")

            self.populate_file_list(self.temp_dir)
            self.show_loading(False)
            self.notify(f"Repository cloned successfully: {self.repo_name}")
            self.query_one("#export_name").value = f"{self.repo_name}.txt"

        except Exception as e:
            self.show_error(f"Error: {str(e)}")
            if self.temp_dir:
                shutil.rmtree(self.temp_dir)
                self.temp_dir = None
        finally:
            self.show_loading(False)

    def populate_file_list(self, root_path):
        selections = []
        excluded_files = {
            "tsconfig.types.json",
            "LICENSE",
            "Dockerfile",
            "bower.json",
            "package-lock.json",
            "tsconfig.json",
            "docker-compose.yml",
        }
        excluded_patterns = {"config.json", "config.js"}

        # Helper function to sort files and directories
        def sort_key(item):
            path = item.value
            parts = path.split(os.sep)
            # Prioritize README and all-caps files
            if parts[-1].upper() == "README.MD" or parts[-1].isupper():
                return (0, path.lower())
            # Then other top-level files
            elif len(parts) == 1:
                return (1, path.lower())
            # Then sort by directory structure
            else:
                return (2, path.lower())

        file_dict = defaultdict(list)

        for root, dirs, files in os.walk(root_path):
            if ".git" in dirs:
                dirs.remove(".git")

            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for file in files:
                if (
                    file.startswith(".") and file != ".cursorrules"
                ) or file in excluded_files:
                    continue
                if any(file.endswith(pattern) for pattern in excluded_patterns):
                    continue
                file_path = os.path.join(root, file)
                relative_path = os.path.relpath(file_path, root_path)
                file_dict[os.path.dirname(relative_path)].append(relative_path)

        # Sort files within each directory
        for directory in file_dict:
            file_dict[directory].sort(key=lambda x: x.lower())

        # Create selections in the desired order
        for path in sorted(file_dict.keys()):
            for file in file_dict[path]:
                selections.append(Selection(file, file))

        # Sort the final list according to the specified priorities
        self.file_selections = sorted(selections, key=sort_key)

        # Focus the SelectionList after populating
        self.call_after_refresh(self.focus_selection_list)

    def focus_selection_list(self):
        self.query_one("#file_list").focus()

    @work
    async def action_export(self):
        if not self.temp_dir:
            self.show_error("No repository cloned. Please fetch files first.")
            return

        file_list = self.query_one("#file_list")
        selected_files = file_list.selected

        if not selected_files:
            self.show_error("No files selected")
            return

        self.show_loading(True)
        self.show_error("")

        try:
            content = ""
            for relative_path in selected_files:
                file_path = os.path.join(self.temp_dir, relative_path)
                if os.path.isfile(file_path):
                    with open(file_path, "r", encoding="utf-8") as f:
                        file_content = f.read()
                    content += (
                        f"File: {relative_path}\n\n```\n{file_content}\n```\n\n###\n\n"
                    )

            export_name = (
                self.query_one("#export_name").value or f"{self.repo_name}.txt"
            )
            with open(export_name, "w", encoding="utf-8") as f:
                f.write(content.rstrip("\n\n###\n\n"))

            self.show_loading(False)
            self.notify(f"Files exported to {export_name}", title="Export Successful")

            # Schedule automatic exit
            self.exit_timer = self.set_timer(2, self.exit_app)
            self.notify(
                "Automatically exiting in 2 seconds. Press 'c' to cancel.", timeout=2
            )

        except Exception as e:
            self.show_error(f"Error exporting files: {str(e)}")
        finally:
            self.show_loading(False)

    def action_cancel_exit(self) -> None:
        """Cancel the automatic exit."""
        if self.exit_timer:
            self.exit_timer.stop()
            self.exit_timer = None
            self.notify("Automatic exit cancelled.")

    def exit_app(self) -> None:
        """Exit the application."""
        self.exit()

    @on(Timer)
    def update_exit_message(self, event: Timer) -> None:
        """Update the exit message countdown."""
        if self.exit_timer:
            remaining = max(0, 2 - int(event.time))
            if remaining > 0:
                self.notify(
                    f"Automatically exiting in {remaining} seconds. Press 'c' to cancel.",
                    timeout=1,
                )

    def on_unmount(self) -> None:
        if self.exit_timer:
            self.exit_timer.stop()
        if self.temp_dir:
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"Error cleaning up temporary directory: {str(e)}")

    def show_loading(self, is_loading: bool):
        self.query_one("#loading").styles.display = "block" if is_loading else "none"

    def show_error(self, message: str):
        self.query_one("#error_message").update(message)
        self.show_loading(False)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="GitHub Repository Exporter")
    parser.add_argument("url", nargs="?", help="GitHub repository URL")
    args = parser.parse_args()

    app = GitHubExporter()
    if args.url:
        app.cli_url = args.url
    app.run()


if __name__ == "__main__":
    main()
