import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer, Horizontal
from textual.widgets import (
    Button,
    Header,
    Footer,
    Input,
    Tree,
    Static,
    Label,
    LoadingIndicator,
)
from textual.widgets.tree import TreeNode
from textual import work


class FileTreeNode(TreeNode):
    """A custom TreeNode that represents a file or directory."""

    def __init__(self, label: str, path: str, is_directory: bool = False):
        super().__init__(label, expanded=False)
        self.path = path
        self.is_directory = is_directory
        self.checked = False
        self.last_modified = datetime.fromtimestamp(os.path.getmtime(path)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    def render(self) -> Text:
        checkbox = "☑" if self.checked else "☐"
        icon = "📁" if self.is_directory else "📄"
        return Text(f"{checkbox} {icon} {self.label} - {self.last_modified}")

    def on_click(self):
        if self.is_directory:
            self.expanded = not self.expanded
            self.toggle_children()
        self.checked = not self.checked
        self.tree.refresh()

    def toggle_children(self):
        for child in self.children:
            child.checked = self.checked
            if isinstance(child, FileTreeNode) and child.is_directory:
                child.toggle_children()


class GitHubExporter(App):
    """Main application class for GitHub file exporter."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "fetch", "Fetch Files"),
        ("e", "export", "Export Selected"),
    ]

    CSS = """
    #url_input {
        dock: top;
        margin: 1 1;
    }

    #file_tree {
        height: 1fr;
        border: solid green;
    }

    #loading {
        align: center middle;
    }

    #error_message {
        color: red;
        text-align: center;
    }

    #export_info {
        height: auto;
    }

    Button {
        margin: 1 2;
    }

    .horizontal-container {
        height: auto;
        align: center middle;
    }
    """

    def __init__(self):
        super().__init__()
        self.temp_dir = None
        self.repo_name = ""
        self.cli_url = ""

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        yield Input(id="url_input", placeholder="Enter GitHub URL")
        yield Horizontal(
            Button("Fetch Files", id="fetch_button"),
            Button("Export Selected", id="export_button"),
            classes="horizontal-container",
        )
        yield Tree("Repository", id="file_tree")
        yield LoadingIndicator(id="loading")
        yield Label("", id="error_message")
        yield Static("", id="export_info")
        yield Footer()

    def on_mount(self):
        """Called when app is mounted in the dom."""
        self.query_one("#loading").display = False
        url_input = self.query_one("#url_input")
        if self.cli_url:
            url_input.value = self.cli_url
            self.action_fetch()
        else:
            url_input.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "fetch_button":
            self.action_fetch()
        elif event.button.id == "export_button":
            self.action_export()

    @work
    async def action_fetch(self):
        """Fetch files from the GitHub repository using a shallow clone."""
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

            # Create a temporary directory for the clone
            self.temp_dir = tempfile.mkdtemp()

            # Perform a shallow clone
            clone_command = f"git clone --depth 1 {url} {self.temp_dir}"
            process = await asyncio.create_subprocess_shell(
                clone_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise Exception(f"Git clone failed: {stderr.decode()}")

            # Populate the tree with the cloned files
            self.populate_tree(self.temp_dir)
            self.show_loading(False)
            self.show_export_info(f"Repository cloned successfully: {self.repo_name}")

        except Exception as e:
            self.show_error(f"Error: {str(e)}")
            if self.temp_dir:
                shutil.rmtree(self.temp_dir)
                self.temp_dir = None
        finally:
            self.show_loading(False)

    def populate_tree(self, root_path):
        """Populate the tree with files and directories from the cloned repository."""
        tree = self.query_one("#file_tree")
        tree.root.remove_children()

        def add_node(path: str, parent: TreeNode):
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                if item == ".git":
                    continue
                if os.path.isdir(item_path):
                    node = FileTreeNode(item, item_path, is_directory=True)
                    parent.add(node)
                    add_node(item_path, node)
                elif os.path.splitext(item)[1].lower() in ALLOWED_EXTENSIONS:
                    node = FileTreeNode(item, item_path)
                    parent.add(node)

        add_node(root_path, tree.root)
        tree.root.expand()

    @work
    async def action_export(self):
        """Export selected files."""
        if not self.temp_dir:
            self.show_error("No repository cloned. Please fetch files first.")
            return

        tree = self.query_one("#file_tree")
        selected_files = [
            node
            for node in tree.walk_tree()
            if node.data and node.data["is_file"] and node.is_checked
        ]

        if not selected_files:
            self.show_error("No files selected")
            return

        self.show_loading(True)
        self.show_error("")

        try:
            content = ""
            for node in selected_files:
                file_path = os.path.join(self.temp_dir, node.data["path"])
                with open(file_path, "r", encoding="utf-8") as f:
                    file_content = f.read()
                content += f"File: {node.data['path']}\n```\n{file_content}\n```\n\n"

            output_file = f"{self.repo_name}.txt"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(content)

            self.show_loading(False)
            self.show_export_info(f"Files exported to {output_file}")

        except Exception as e:
            self.show_error(f"Error exporting files: {str(e)}")
        finally:
            self.show_loading(False)

    def on_unmount(self) -> None:
        """Clean up temporary directory when the app is closed."""
        if self.temp_dir:
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                print(f"Error cleaning up temporary directory: {str(e)}")

    def show_loading(self, is_loading: bool):
        """Show or hide the loading indicator."""
        self.query_one("#loading").display = is_loading

    def show_error(self, message: str):
        """Show an error message."""
        self.query_one("#error_message").update(message)
        self.show_loading(False)

    def show_export_info(self, message: str):
        """Show export information."""
        self.query_one("#export_info").update(message)


ALLOWED_EXTENSIONS = (".md", ".txt", ".js", ".ts", ".py")


def main():
    """Main function to run the app."""
    app = GitHubExporter()
    if len(sys.argv) > 1:
        app.cli_url = sys.argv[1]
    app.run()


if __name__ == "__main__":
    main()
