"""Source-only adapters for desktop CAD applications.

These compilers only produce scripts.  PromptCAD never imports the proprietary
SDKs or starts desktop CAD processes in the API server.
"""

from __future__ import annotations

import json

from app.models.cad import CadDocument

_PAYLOAD_MARKER = "__PROMPTCAD_DOCUMENT_JSON__"

_FUSION_360_SCRIPT = r'''"""Run inside Autodesk Fusion 360 as a script or add-in."""
from __future__ import annotations

import json
import traceback
from pathlib import Path

import adsk.core
import adsk.fusion

CAD_SPEC = json.loads(__PROMPTCAD_DOCUMENT_JSON__)


def run(context):
    app = adsk.core.Application.get()
    user_interface = app.userInterface
    try:
        # Only the validated STEP artifact beside this adapter is accepted.
        source_step = Path(__file__).resolve().with_name("model.step")
        if not source_step.is_file():
            raise FileNotFoundError(f"validated sibling STEP not found: {source_step.name}")

        # Always isolate the import in a new document.  Never mutate or archive
        # whatever design the operator currently has open.
        app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
        if design is None:
            raise RuntimeError("Fusion did not create an active design")
        root_component = design.rootComponent

        import_manager = app.importManager
        import_options = import_manager.createSTEPImportOptions(str(source_step))
        if not import_manager.importToTarget(import_options, root_component):
            raise RuntimeError("Fusion STEP import failed")

        output_file = source_step.with_name("model.f3d")
        export_manager = design.exportManager
        archive_options = export_manager.createFusionArchiveExportOptions(str(output_file))
        if not export_manager.execute(archive_options):
            raise RuntimeError("Fusion archive export failed")
        user_interface.messageBox(f"PromptCAD import complete: {output_file.name}")
    except Exception:
        user_interface.messageBox("PromptCAD adapter failed:\n" + traceback.format_exc())
        raise


def stop(context):
    return None
'''

_SOLIDWORKS_SCRIPT = r'''"""Run with desktop Python and an installed SOLIDWORKS instance."""
from __future__ import annotations

import json
from pathlib import Path

import pythoncom
import win32com.client

CAD_SPEC = json.loads(__PROMPTCAD_DOCUMENT_JSON__)

SW_DOC_PART = 1
SW_OPEN_SILENT = 1
SW_SAVE_CURRENT_VERSION = 0
SW_SAVE_SILENT = 1


def main():
    # Only the validated STEP artifact beside this adapter is accepted.
    source_step = Path(__file__).resolve().with_name("model.step")
    if not source_step.is_file():
        raise FileNotFoundError(f"validated sibling STEP not found: {source_step.name}")
    output_file = source_step.with_name("model.SLDPRT")

    pythoncom.CoInitialize()
    try:
        application = win32com.client.Dispatch("SldWorks.Application")
        application.Visible = True
        errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        model = application.OpenDoc6(
            str(source_step),
            SW_DOC_PART,
            SW_OPEN_SILENT,
            "",
            errors,
            warnings,
        )
        if model is None:
            raise RuntimeError(
                "SOLIDWORKS could not open STEP "
                f"(errors={errors.value}, warnings={warnings.value})"
            )
        errors.value = 0
        warnings.value = 0
        if not model.Extension.SaveAs(
            str(output_file),
            SW_SAVE_CURRENT_VERSION,
            SW_SAVE_SILENT,
            None,
            errors,
            warnings,
        ):
            raise RuntimeError(
                "SOLIDWORKS native save failed "
                f"(errors={errors.value}, warnings={warnings.value})"
            )
    finally:
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
'''


def _payload(doc: CadDocument) -> str:
    return json.dumps(
        doc.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class Fusion360AdapterCompiler:
    """Generate a Fusion 360 import/archive script without loading its SDK."""

    def compile(self, doc: CadDocument) -> str:
        return _FUSION_360_SCRIPT.replace(_PAYLOAD_MARKER, repr(_payload(doc)), 1)


class SolidWorksAdapterCompiler:
    """Generate a SOLIDWORKS STEP-to-SLDPRT adapter without loading COM."""

    def compile(self, doc: CadDocument) -> str:
        return _SOLIDWORKS_SCRIPT.replace(_PAYLOAD_MARKER, repr(_payload(doc)), 1)
