import frappe
from frappe import _
from frappe.core.doctype.file.file import File
from frappe.core.doctype.file.utils import *
from frappe_s3_attachment.controller import S3Operations, file_upload_to_s3, delete_from_cloud
from frappe.utils import get_url
from frappe.utils.file_manager import is_safe_path
import urllib.parse

URL_PREFIXES = ("http://", "https://")


class NonaFile(File):
    def before_insert(self):
        self.set_folder_name()
        self.set_file_name()
        self.validate_attachment_limit()

        if self.is_folder:
            return

        if self.is_remote_file:
            self.validate_remote_file()
        elif self.file_url and self.file_url.startswith("/api/method/frappe_s3_attachment.controller.generate_file"):
            pass
        else:
            self.save_file(content=self.get_content())
            self.flags.new_file = True
            frappe.local.rollback_observers.append(self)

        file_upload_to_s3(self, "method")

    def validate_file_path(self):
        if self.is_remote_file:
            return

    def validate_file_url(self):
        if self.is_remote_file or not self.file_url:
            return

        if not self.file_url.startswith(("/api/method/frappe_s3_attachment.controller.generate_file")):
            # Probably an invalid URL since it doesn't start with api either for private files
            frappe.throw(
                _("URL must start with http:// or https://"),
                title=_("Invalid URL"),
            )

    def validate_file_on_disk(self):
        """Validates existence file"""
        full_path = self.get_full_path()

        if full_path.startswith(URL_PREFIXES):
            return True

    def get_content(self) -> bytes:
        if self.is_folder:
            frappe.throw(_("Cannot get file contents of a Folder"))

        if self.get("content"):
            self._content = self.content
            if self.decode:
                self._content = decode_file_content(self._content)
                self.decode = False
            # self.content = None # TODO: This needs to happen; make it happen somehow
            return self._content

        if self.file_url:
            self.validate_file_url()
        file_path = self.get_full_path()
        s3 = S3Operations()

        # read the file
        if file_path.startswith(f"""https://s3.{s3.s3_settings_doc.region_name}.amazonaws.com/{s3.BUCKET}"""):
            key = file_path[file_path.index(
                f"""/{s3.BUCKET}/""")+len(f"""/{s3.BUCKET}/"""):]
            response = s3.read_file_from_s3(key)
            file_data = response.get('Body').read()
            self._content = file_data
            try:
                # for plain text files
                self._content = self._content.decode()
            except UnicodeDecodeError:
                # for .png, .jpg, etc
                pass
        elif file_path.startswith("/api/method/frappe_s3_attachment.controller.generate_file"):
            url = urllib.parse.parse_qs(file_path.split("generate_file?")[1])
            key = url['key'][0]
            response = s3.read_file_from_s3(key)
            file_data = response.get('Body').read()
            self._content = file_data
            try:
                # for plain text files
                self._content = self._content.decode()
            except UnicodeDecodeError:
                # for .png, .jpg, etc
                pass
        else:
            with open(file_path, mode="rb") as f:
                self._content = f.read()
                try:
                    # for plain text files
                    self._content = self._content.decode()
                except UnicodeDecodeError:
                    # for .png, .jpg, etc
                    pass

        return self._content

    def get_full_path(self):
        """Returns file path from given file name"""

        file_path = self.file_url or self.file_name

        site_url = get_url()
        if "/files/" in file_path and file_path.startswith(site_url):
            file_path = file_path.split(site_url, 1)[1]

        if "/" not in file_path:
            if self.is_private:
                file_path = f"/private/files/{file_path}"
            else:
                file_path = f"/files/{file_path}"

        if file_path.startswith("/private/files/"):
            file_path = get_files_path(
                *file_path.split("/private/files/", 1)[1].split("/"), is_private=1)

        elif file_path.startswith("/files/"):
            file_path = get_files_path(*file_path.split("/files/", 1)[1].split("/"))

        elif file_path.startswith(URL_PREFIXES):
            pass

        elif file_path.startswith("/api/method/frappe_s3_attachment"):
            return file_path

        elif not self.file_url:
            frappe.throw(
                _("There is some problem with the file url: {0}").format(file_path))

        if not is_safe_path(file_path):
            frappe.throw(_("Cannot access file path {0}").format(file_path))

        if os.path.sep in self.file_name:
            frappe.throw(_("File name cannot have {0}").format(os.path.sep))

        return file_path

    def create_attachment_record(self):
        icon = ' <i class="fa fa-lock text-warning"></i>' if self.is_private else ""
        file_url = self.file_url
        file_name = self.file_name or self.file_url

        self.add_comment_in_reference_doc(
            "Attachment",
            _("Added {0}").format(
                f"<a href='{file_url}' target='_blank'>{file_name}</a>{icon}"),
        )

    def on_trash(self):
        delete_from_cloud(self)
