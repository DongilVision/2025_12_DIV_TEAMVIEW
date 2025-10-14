import sys
import os
import re
import json
from datetime import datetime
from typing import List
import numpy as np
import cv2

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QMessageBox,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QComboBox, QScrollArea, QGridLayout, QTextEdit, QProgressBar, QSizePolicy,
    QDoubleSpinBox, QTabWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette, QColor


# ---------- 공통 유틸 ----------
def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]

def imread_unicode(path: str):
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        return img
    except Exception:
        return None

def imwrite_unicode(path: str, image: np.ndarray, ext: str):
    params = []
    lower = ext.lower()
    if lower in (".jpg", ".jpeg"):
        params = [cv2.IMWRITE_JPEG_QUALITY, 95]
    elif lower == ".png":
        params = [cv2.IMWRITE_PNG_COMPRESSION, 3]

    success, buf = cv2.imencode(ext, image, params)
    if not success:
        return False
    try:
        # 한글 경로 대응
        os.makedirs(os.path.dirname(path), exist_ok=True)
        buf.tofile(path)
        return True
    except Exception:
        return False

def apply_dark_theme(app: QApplication):
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(37, 37, 38))
    palette.setColor(QPalette.WindowText, QColor(224, 224, 224))
    palette.setColor(QPalette.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.AlternateBase, QColor(37, 37, 38))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.Text, QColor(224, 224, 224))
    palette.setColor(QPalette.Button, QColor(45, 45, 48))
    palette.setColor(QPalette.ButtonText, QColor(224, 224, 224))
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Highlight, QColor(58, 110, 165))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.PlaceholderText, QColor(160, 160, 160))
    app.setPalette(palette)

    app.setStyleSheet("""
        QWidget { color: #E0E0E0; }
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
            background-color: #1e1e1e;
            border: 1px solid #444;
            border-radius: 6px;
            padding: 4px 6px;
        }
        QScrollArea { border: 0px; background: #252526; }
        QPushButton {
            background-color: #2d2d2d;
            border: 1px solid #555;
            border-radius: 6px;
            padding: 6px 10px;
        }
        QPushButton:hover { border-color: #777; }
        QPushButton:checked {
            background-color: #3a6ea5;
            border: 1px solid #3a6ea5;
        }
        QProgressBar {
            text-align: center;
            border: 1px solid #555;
            border-radius: 6px;
            background: #1e1e1e;
        }
        QProgressBar::chunk { background-color: #3a6ea5; }
        QTabBar::tab:selected { background: #2d2d2d; }
        QTabBar::tab:!selected { background: #1e1e1e; }
    """)


# ---------- 탭1: 그룹 추출 ----------
class GroupingTab(QWidget):
    def __init__(self):
        super().__init__()
        self.input_dir = ""
        self.output_dir = ""
        self.index_buttons: List[QPushButton] = []

        # 상단: 폴더 선택
        input_label = QLabel("입력 폴더")
        self.input_edit = QLineEdit(); self.input_edit.setReadOnly(True)
        input_btn = QPushButton("선택"); input_btn.clicked.connect(self.browse_input)

        output_label = QLabel("출력 폴더")
        self.output_edit = QLineEdit(); self.output_edit.setReadOnly(True)
        output_btn = QPushButton("선택"); output_btn.clicked.connect(self.browse_output)

        top_grid = QGridLayout()
        top_grid.addWidget(input_label, 0, 0)
        top_grid.addWidget(self.input_edit, 0, 1)
        top_grid.addWidget(input_btn, 0, 2)
        top_grid.addWidget(output_label, 1, 0)
        top_grid.addWidget(self.output_edit, 1, 1)
        top_grid.addWidget(output_btn, 1, 2)

        # 설정(포맷/그룹 크기)
        fmt_label = QLabel("저장 포맷")
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["jpeg", "jpg", "png"])
        self.fmt_combo.setCurrentText("jpeg")

        group_label = QLabel("그룹 크기 (2~6)")
        self.group_spin = QSpinBox(); self.group_spin.setRange(2, 6); self.group_spin.setValue(6)
        self.group_spin.valueChanged.connect(self.rebuild_index_buttons)

        setting_layout = QHBoxLayout()
        setting_layout.addWidget(fmt_label)
        setting_layout.addWidget(self.fmt_combo)
        setting_layout.addSpacing(24)
        setting_layout.addWidget(group_label)
        setting_layout.addWidget(self.group_spin)
        setting_layout.addStretch(1)

        # 동적 버튼 영역
        self.buttons_container = QWidget()
        self.buttons_layout = QHBoxLayout()
        self.buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.buttons_layout.setSpacing(8)
        self.buttons_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.buttons_container.setLayout(self.buttons_layout)

        btns_scroll = QScrollArea()
        btns_scroll.setWidgetResizable(True)
        btns_scroll.setWidget(self.buttons_container)
        btns_scroll.setFixedHeight(80)

        sel_all_btn = QPushButton("전체 선택"); sel_all_btn.clicked.connect(self.select_all_indices)
        clear_all_btn = QPushButton("전체 해제"); clear_all_btn.clicked.connect(self.clear_all_indices)

        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("그룹 내 추출 순번 선택"))
        sel_row.addStretch(1)
        sel_row.addWidget(sel_all_btn)
        sel_row.addWidget(clear_all_btn)

        # 실행/상태
        self.run_btn = QPushButton("실행"); self.run_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.run_btn.clicked.connect(self.run)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)

        run_row = QHBoxLayout()
        run_row.addWidget(self.run_btn)
        run_row.addSpacing(16)
        run_row.addWidget(self.progress, 1)

        # 로그
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("처리 로그")

        # 메인 레이아웃
        main_v = QVBoxLayout(self)
        main_v.addLayout(top_grid)
        main_v.addSpacing(6)
        main_v.addLayout(setting_layout)
        main_v.addLayout(sel_row)
        main_v.addWidget(btns_scroll)
        main_v.addSpacing(6)
        main_v.addLayout(run_row)
        main_v.addWidget(self.log, 1)

        self.rebuild_index_buttons()

    @staticmethod
    def _clear_layout(layout: QHBoxLayout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def browse_input(self):
        path = QFileDialog.getExistingDirectory(self, "입력 폴더 선택")
        if path:
            self.input_dir = path
            self.input_edit.setText(path)

    def browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "출력 폴더 선택")
        if path:
            self.output_dir = path
            self.output_edit.setText(path)

    def rebuild_index_buttons(self):
        self._clear_layout(self.buttons_layout)
        self.index_buttons.clear()
        n = self.group_spin.value()
        for i in range(1, n + 1):
            btn = QPushButton(str(i))
            btn.setCheckable(True)
            btn.setFixedWidth(48)
            btn.setMinimumHeight(32)
            self.index_buttons.append(btn)
            self.buttons_layout.addWidget(btn)
        self.buttons_layout.addStretch(1)
        self.buttons_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def select_all_indices(self):
        for b in self.index_buttons:
            b.setChecked(True)

    def clear_all_indices(self):
        for b in self.index_buttons:
            b.setChecked(False)

    def _append_log(self, text: str):
        self.log.append(text)
        self.log.ensureCursorVisible()

    def run(self):
        if not self.input_dir or not os.path.isdir(self.input_dir):
            QMessageBox.warning(self, "경고", "유효한 입력 폴더를 선택하십시오.")
            return
        if not self.output_dir or not os.path.isdir(self.output_dir):
            QMessageBox.warning(self, "경고", "유효한 출력 폴더를 선택하십시오.")
            return

        fmt = self.fmt_combo.currentText().lower()
        if fmt not in ("png", "jpg", "jpeg"):
            QMessageBox.warning(self, "경고", "저장 포맷은 png, jpg, jpeg 중 하나여야 합니다.")
            return
        out_ext = f".{fmt}"

        group_size = self.group_spin.value()
        selected_positions = [i for i, b in enumerate(self.index_buttons, start=1) if b.isChecked()]
        if not selected_positions:
            QMessageBox.warning(self, "경고", "그룹 내에서 추출할 순번(버튼)을 하나 이상 선택하십시오.")
            return

        in_files = []
        allow_in = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
        for name in os.listdir(self.input_dir):
            if os.path.isfile(os.path.join(self.input_dir, name)):
                ext = os.path.splitext(name)[1].lower()
                if ext in allow_in:
                    in_files.append(name)
        in_files.sort(key=natural_key)

        total = len(in_files)
        if total == 0:
            QMessageBox.information(self, "정보", "입력 폴더에 처리 가능한 이미지가 없습니다.")
            return

        full_groups = total // group_size
        remainder = total % group_size

        self.log.clear()
        self._append_log(f"입력 파일 수: {total}")
        self._append_log(f"그룹 크기: {group_size}")
        self._append_log(f"완전한 그룹 수: {full_groups} (남는 이미지: {remainder}개는 무시)")
        self._append_log(f"추출 순번: {selected_positions}")
        self.progress.setValue(0)

        saved = 0
        processed_groups = 0
        total_targets = full_groups * len(selected_positions) if full_groups > 0 else 0

        for g in range(full_groups):
            start = g * group_size
            for pos in selected_positions:
                idx = start + (pos - 1)
                if idx >= total:
                    continue
                fname = in_files[idx]
                in_path = os.path.join(self.input_dir, fname)

                img = imread_unicode(in_path)
                if img is None:
                    self._append_log(f"[건너뜀] 읽기 실패: {fname}")
                    continue

                base, _ = os.path.splitext(fname)
                out_name = base + out_ext
                out_path = os.path.join(self.output_dir, out_name)

                ok = imwrite_unicode(out_path, img, out_ext)
                if not ok:
                    self._append_log(f"[실패] 저장 실패: {out_name}")
                else:
                    saved += 1
                    self._append_log(f"[저장] {out_name}")

            processed_groups += 1
            if total_targets > 0:
                progress = int((min(processed_groups * len(selected_positions), total_targets) / total_targets) * 100)
                self.progress.setValue(progress)
                QApplication.processEvents()

        self.progress.setValue(100)
        self._append_log("-----")
        self._append_log(f"완료: 저장 {saved}장")
        if remainder > 0:
            self._append_log(f"참고: {remainder}개의 남는 이미지는 그룹 미완성으로 미처리")
        QMessageBox.information(self, "완료", f"처리를 완료했습니다.\n저장 {saved}장")


# ---------- 탭2: YOLOv8 추론 ----------
class YoloInferTab(QWidget):
    def __init__(self):
        super().__init__()
        self.input_dir = ""
        self.model_path = ""
        self.json_path = ""

        # 입력 폴더
        in_label = QLabel("입력 폴더")
        self.in_edit = QLineEdit(); self.in_edit.setReadOnly(True)
        in_btn = QPushButton("선택"); in_btn.clicked.connect(self.browse_input)

        # 모델 경로
        model_label = QLabel("YOLOv8 모델(.pt)")
        self.model_edit = QLineEdit(); self.model_edit.setReadOnly(True)
        model_btn = QPushButton("선택"); model_btn.clicked.connect(self.browse_model)

        # 기본 모델 경로 설정 (프로젝트 루트의 model/best.pt)
        try:
            default_model = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "model", "best.pt"))
            if os.path.isfile(default_model):
                self.model_path = default_model
                self.model_edit.setText(default_model)
        except Exception:
            pass

        # 결과 JSON 경로
        json_label = QLabel("결과 JSON 경로")
        self.json_edit = QLineEdit(); self.json_edit.setPlaceholderText("예) C:/result/results.json")
        json_btn = QPushButton("선택"); json_btn.clicked.connect(self.browse_json)

        # 파라미터
        conf_label = QLabel("conf")
        self.conf_spin = QDoubleSpinBox(); self.conf_spin.setRange(0.0, 1.0); self.conf_spin.setSingleStep(0.01); self.conf_spin.setValue(0.25)
        imgsz_label = QLabel("imgsz")
        self.imgsz_spin = QSpinBox(); self.imgsz_spin.setRange(32, 4096); self.imgsz_spin.setValue(640)
        device_label = QLabel("device")
        self.device_combo = QComboBox(); self.device_combo.addItems(["auto", "cuda", "cpu"]); self.device_combo.setCurrentText("auto")

        # 상단 그리드
        grid = QGridLayout()
        grid.addWidget(in_label,     0, 0); grid.addWidget(self.in_edit,    0, 1); grid.addWidget(in_btn,    0, 2)
        grid.addWidget(model_label,  1, 0); grid.addWidget(self.model_edit, 1, 1); grid.addWidget(model_btn, 1, 2)
        grid.addWidget(json_label,   2, 0); grid.addWidget(self.json_edit,  2, 1); grid.addWidget(json_btn,  2, 2)

        param_row = QHBoxLayout()
        param_row.addWidget(conf_label);  param_row.addWidget(self.conf_spin)
        param_row.addSpacing(16)
        param_row.addWidget(imgsz_label); param_row.addWidget(self.imgsz_spin)
        param_row.addSpacing(16)
        param_row.addWidget(device_label); param_row.addWidget(self.device_combo)
        param_row.addStretch(1)

        # 실행/상태
        self.run_btn = QPushButton("추론 실행"); self.run_btn.clicked.connect(self.run_infer)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)

        run_row = QHBoxLayout()
        run_row.addWidget(self.run_btn)
        run_row.addSpacing(16)
        run_row.addWidget(self.progress, 1)

        # 로그
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("추론 로그")

        # 레이아웃
        main_v = QVBoxLayout(self)
        main_v.addLayout(grid)
        main_v.addSpacing(6)
        main_v.addLayout(param_row)
        main_v.addSpacing(6)
        main_v.addLayout(run_row)
        main_v.addWidget(self.log, 1)

    def _append_log(self, text: str):
        self.log.append(text)
        self.log.ensureCursorVisible()

    def browse_input(self):
        path = QFileDialog.getExistingDirectory(self, "입력 폴더 선택")
        if path:
            self.input_dir = path
            self.in_edit.setText(path)

    def browse_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "YOLOv8 모델(.pt) 선택", filter="PyTorch Model (*.pt)")
        if path:
            self.model_path = path
            self.model_edit.setText(path)

    def browse_json(self):
        # 기본 파일명: 현재 날짜시간
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = self.in_edit.text().strip() if self.in_edit.text().strip() else os.path.expanduser("~")
        default_path = os.path.join(base_dir, f"{timestamp}.json")

        path, _ = QFileDialog.getSaveFileName(self, "결과 JSON 경로 지정", dir=default_path, filter="JSON (*.json)")
        if path:
            self.json_path = path
            self.json_edit.setText(path)

    def _resolve_device(self):
        choice = self.device_combo.currentText().lower()
        if choice == "auto":
            try:
                import torch
                return "0" if torch.cuda.is_available() else "cpu"
            except Exception:
                return "cpu"
        elif choice == "cuda":
            return "0"
        else:
            return "cpu"

    def run_infer(self):
        # 검증
        if not self.input_dir or not os.path.isdir(self.input_dir):
            QMessageBox.warning(self, "경고", "유효한 입력 폴더를 선택하십시오.")
            return
        if not self.model_path or not os.path.isfile(self.model_path):
            QMessageBox.warning(self, "경고", "유효한 YOLOv8 모델(.pt) 경로를 선택하십시오.")
            return
        out_json = self.json_edit.text().strip()
        if not out_json:
            QMessageBox.warning(self, "경고", "결과 JSON 경로를 지정하십시오.")
            return

        # 이미지 목록
        allow_in = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
        files = [n for n in os.listdir(self.input_dir)
                 if os.path.isfile(os.path.join(self.input_dir, n)) and os.path.splitext(n)[1].lower() in allow_in]
        files.sort(key=natural_key)
        if not files:
            QMessageBox.information(self, "정보", "입력 폴더에 처리 가능한 이미지가 없습니다.")
            return

        # YOLO 로드
        try:
            from ultralytics import YOLO
        except Exception as e:
            QMessageBox.critical(self, "오류", f"ultralytics를 불러오지 못했습니다. 설치를 확인하십시오.\n{e}")
            return

        conf = float(self.conf_spin.value())
        imgsz = int(self.imgsz_spin.value())
        device = self._resolve_device()

        self.log.clear()
        self._append_log(f"모델: {self.model_path}")
        self._append_log(f"입력 폴더: {self.input_dir}")
        self._append_log(f"conf={conf}, imgsz={imgsz}, device={device}")
        self.progress.setValue(0)

        try:
            model = YOLO(self.model_path)
        except Exception as e:
            QMessageBox.critical(self, "오류", f"모델 로드 실패:\n{e}")
            return

        results_list = []
        total = len(files)

        for i, fname in enumerate(files, start=1):
            fpath = os.path.join(self.input_dir, fname)
            try:
                # 단일 이미지 추론
                results = model.predict(
                    source=fpath, conf=conf, imgsz=imgsz, device=device, verbose=False
                )
                if not results:
                    centers = []
                else:
                    r0 = results[0]
                    centers = []
                    if hasattr(r0, "boxes") and r0.boxes is not None and r0.boxes.xyxy is not None:
                        xyxy = r0.boxes.xyxy.cpu().numpy() if hasattr(r0.boxes.xyxy, "cpu") else np.array(r0.boxes.xyxy)
                        for (x1, y1, x2, y2) in xyxy:
                            cx = float((x1 + x2) / 2.0)
                            cy = float((y1 + y2) / 2.0)
                            centers.append({"x": cx, "y": cy})
                results_list.append({"image": fname, "centers": centers})
                self._append_log(f"[{i}/{total}] {fname} → {len(centers)}개")
            except Exception as e:
                self._append_log(f"[오류] {fname} → {e}")
                results_list.append({"image": fname, "centers": []})

            self.progress.setValue(int(i * 100 / total))
            QApplication.processEvents()

        # JSON 저장
        try:
            os.makedirs(os.path.dirname(out_json), exist_ok=True)
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(results_list, f, ensure_ascii=False, indent=2)
        except Exception as e:
            QMessageBox.critical(self, "오류", f"JSON 저장 실패:\n{e}")
            return

        self._append_log(f"완료: {out_json}")
        QMessageBox.information(self, "완료", f"추론이 완료되었습니다.\nJSON: {out_json}")


# ---------- 메인 윈도우 ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("이미지 유틸 & YOLOv8 추론 (PySide6)")
        self.setMinimumSize(980, 720)

        tabs = QTabWidget()
        tabs.addTab(GroupingTab(), "그룹 추출")
        tabs.addTab(YoloInferTab(), "YOLOv8 추론")
        self.setCentralWidget(tabs)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
