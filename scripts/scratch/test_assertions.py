from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget, QComboBox
import sys

# Simulation of what I want to check
def test_assertions():
    app = QApplication([])
    
    # Mocking SettingsDialog
    class MockSettingsDialog:
        def __init__(self):
            self.tabs = QTabWidget()
            self.provider_combo = QComboBox()
            self.tabs.addTab(QComboBox(), "Khóa API") # index 0
            self.tabs.addTab(QComboBox(), "General") # index 1
            self.provider_combo.addItem("Gemini", "gemini")
            self.provider_combo.addItem("OpenAI", "openai")

    dlg = MockSettingsDialog()
    
    # Test Keys tab assertion
    pid = "gemini"
    dlg.tabs.setCurrentIndex(0)
    idx = dlg.provider_combo.findData(pid)
    dlg.provider_combo.setCurrentIndex(idx)
    
    assert dlg.tabs.currentIndex() == 0
    assert dlg.provider_combo.currentData() == pid
    print("Assertion 1 passed")

    # Test Tab N assertion
    index = 1
    dlg.tabs.setCurrentIndex(index)
    assert dlg.tabs.currentIndex() == index
    print("Assertion 2 passed")

test_assertions()
