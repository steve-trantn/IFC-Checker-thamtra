"""Xuất xung đột ra BCF 2.1 (.bcfzip) - mở được trong Navisworks, BIMcollab, Solibri, Revizto, BlenderBIM..."""

from __future__ import annotations

import uuid
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from . import APP_NAME, AUTHOR


def _vp_xml(c, guid_vp: str) -> str:
    # camera nhìn chéo xuống điểm va chạm từ khoảng cách 15 m
    d = 15.0
    cam = (c.x + d * 0.6, c.y - d * 0.6, c.z + d * 0.55)
    direction = (-0.6, 0.6, -0.55)
    n = sum(v * v for v in direction) ** 0.5
    direction = tuple(v / n for v in direction)
    up = (0.0, 0.0, 1.0)
    comps = "".join(f'<Component IfcGuid="{escape(g)}"/>' for g in (c.guid_a, c.guid_b) if g)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<VisualizationInfo Guid="{guid_vp}">'
        f'<Components><Selection>{comps}</Selection>'
        '<Visibility DefaultVisibility="true"/></Components>'
        '<PerspectiveCamera>'
        f'<CameraViewPoint><X>{cam[0]:.3f}</X><Y>{cam[1]:.3f}</Y><Z>{cam[2]:.3f}</Z></CameraViewPoint>'
        f'<CameraDirection><X>{direction[0]:.4f}</X><Y>{direction[1]:.4f}</Y><Z>{direction[2]:.4f}</Z></CameraDirection>'
        f'<CameraUpVector><X>{up[0]:.1f}</X><Y>{up[1]:.1f}</Y><Z>{up[2]:.1f}</Z></CameraUpVector>'
        '<FieldOfView>60</FieldOfView></PerspectiveCamera>'
        '</VisualizationInfo>'
    )


def export_bcf(clashes, path: str, author: str = "", project_name: str = "Thẩm tra BIM") -> int:
    author = author or AUTHOR
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bcf.version", '<?xml version="1.0" encoding="UTF-8"?>\n'
                                  '<Version VersionId="2.1"><DetailedVersion>2.1</DetailedVersion></Version>')
        z.writestr("project.bcfp", '<?xml version="1.0" encoding="UTF-8"?>\n'
                                   f'<ProjectExtension><Project ProjectId="{uuid.uuid4()}">'
                                   f'<Name>{escape(project_name)}</Name></Project>'
                                   '<ExtensionSchema></ExtensionSchema></ProjectExtension>')
        for c in clashes:
            tg, vg = str(uuid.uuid4()), str(uuid.uuid4())
            files = "".join(f'<File IsExternal="true"><Filename>{escape(f)}</Filename></File>'
                            for f in sorted({c.file_a, c.file_b}))
            measure = getattr(c, "detail", "") or f"Độ sâu xuyên {c.depth:.3f} m"
            desc = (f"Phép thử: {c.test}\n{measure}\n"
                    f"A: {c.class_a} - {c.name_a} ({c.file_a}) GUID {c.guid_a}\n"
                    f"B: {c.class_b} - {c.name_b} ({c.file_b}) GUID {c.guid_b}\n"
                    f"Vị trí: X={c.x:.3f} Y={c.y:.3f} Z={c.z:.3f}"
                    + (f"\nĐịnh nghĩa: {c.note}" if c.note else ""))
            markup = (
                '<?xml version="1.0" encoding="UTF-8"?>\n<Markup>'
                f'<Header>{files}</Header>'
                f'<Topic Guid="{tg}" TopicType="Clash" TopicStatus="Open">'
                f'<Title>{escape(c.id)} - {escape(c.test)}</Title>'
                '<Priority>Normal</Priority>'
                f'<CreationDate>{now}</CreationDate><CreationAuthor>{escape(author)}</CreationAuthor>'
                f'<Description>{escape(desc)}</Description>'
                f'<Labels>{escape(APP_NAME)}</Labels>'
                '</Topic>'
                f'<Viewpoints Guid="{vg}"><Viewpoint>viewpoint.bcfv</Viewpoint></Viewpoints>'
                '</Markup>'
            )
            z.writestr(f"{tg}/markup.bcf", markup)
            z.writestr(f"{tg}/viewpoint.bcfv", _vp_xml(c, vg))
    return len(clashes)
