import sys
import os
import re
import time
import json
import logging
import pdfplumber
import docx
import csv
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QTextEdit, QFileDialog,
    QComboBox, QHBoxLayout, QMessageBox, QProgressBar, QSpinBox,
    QCheckBox, QGroupBox, QCompleter, QScrollArea, QGridLayout
)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThread
from PyQt5.QtGui import QColor, QPalette, QIcon, QFont
from langchain_ollama import OllamaLLM
from langchain.prompts import PromptTemplate

# Constants
class Constants:
    DEFAULT_MODEL = "llama3.2:latest"
    OLLAMA_BASE_URL = "http://localhost:11434"
    DOCKER_OLLAMA_URL = "http://host.docker.internal:11434"
    TEST_TYPES = [
        "Positive", "Negative", "Boundary", "Edge Case",
        "Acceptance (UAT)", "Functional", "Sanity", "Smoke",
        "Accessibility", "Performance", "Exception", "Usability",
        "End-to-End", "Integration", "API", "Regression"
    ]
    DOMAINS = [
        "Banking", "Healthcare", "E-commerce", "Education", "Wireless",
        "Generic", "Insurance", "Aviation", "Telecommunication", "CRM"
    ]
    MAX_INPUT_LENGTH = 10000
    MAX_TEST_CASES = 150
    CONFIG_FILE = "testgen_config.json"
    THREAD_SHUTDOWN_TIMEOUT = 3000  # ms

# Configuration Manager
class ConfigManager:
    def __init__(self):
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        default_config = {
            "ollama_url": Constants.OLLAMA_BASE_URL,
            "default_model": Constants.DEFAULT_MODEL,
            "ui_theme": "light",
            "default_domain": "Generic",
            "default_test_count": 10,
            "recent_files": [],
            "window_size": [1000, 700]
        }

        try:
            config_path = Path.home() / ".config" / Constants.CONFIG_FILE
            if config_path.exists():
                with open(config_path, "r") as f:
                    return {**default_config, **json.load(f)}
            return default_config
        except Exception as e:
            logging.warning(f"Failed to load config: {e}")
            return default_config

    def save_config(self) -> bool:
        try:
            config_path = Path.home() / ".config"
            config_path.mkdir(parents=True, exist_ok=True)
            with open(config_path / Constants.CONFIG_FILE, "w") as f:
                json.dump(self.config, f, indent=2)
            return True
        except Exception as e:
            logging.error(f"Failed to save config: {e}")
            return False

    def add_recent_file(self, file_path: str) -> None:
        if file_path in self.config["recent_files"]:
            self.config["recent_files"].remove(file_path)
        self.config["recent_files"].insert(0, file_path)
        self.config["recent_files"] = self.config["recent_files"][:5]
        self.save_config()

# Model Service
class ModelService:
    @staticmethod
    def get_available_models() -> List[str]:
        try:
            base_url = Constants.DOCKER_OLLAMA_URL if os.environ.get("DOCKER") == "1" else Constants.OLLAMA_BASE_URL
            response = requests.get(f"{base_url}/api/tags", timeout=10)
            response.raise_for_status()
            models_data = response.json().get('models', [])
            return [model['name'] for model in models_data if 'name' in model]
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching models: {e}")
            return [Constants.DEFAULT_MODEL]
        except Exception as e:
            logging.error(f"Unexpected error fetching models: {e}")
            return [Constants.DEFAULT_MODEL]

# File Service
class FileService:
    @staticmethod
    def extract_text(file_path: str) -> str:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".pdf":
                with pdfplumber.open(file_path) as pdf:
                    return "\n".join(p.extract_text() or "" for p in pdf.pages if p.extract_text())
            elif ext == ".docx":
                doc = docx.Document(file_path)
                return "\n".join(p.text for p in doc.paragraphs)
            else:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        return f.read()
                except UnicodeDecodeError:
                    with open(file_path, "r", encoding="latin-1") as f:
                        return f.read()
                except Exception as text_e:
                     raise ValueError(f"File encoding not recognized or other read error: {text_e}")

        except Exception as e:
            raise RuntimeError(f"Failed to extract text: {str(e)}")

    @staticmethod
    def safe_save(content: str, suggested_path: str) -> bool:
        try:
            abs_suggested_path = os.path.abspath(suggested_path)
            base_dir = os.path.abspath(os.path.expanduser("~/Downloads"))

            if not os.path.commonpath([abs_suggested_path, base_dir]) == base_dir:
                file_name = os.path.basename(abs_suggested_path)
                abs_suggested_path = os.path.join(base_dir, file_name)

            if not abs_suggested_path.endswith(".csv"):
                abs_suggested_path += ".csv"

            with open(abs_suggested_path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            return True
        except Exception as e:
            logging.error(f"File save failed: {e}")
            return False

# Test Case Validator
class TestCaseValidator:
    @staticmethod
    def is_valid_test_case(tc_block: str) -> bool:
        required_fields = ["Title:", "Description:", "Test Steps:", "Expected Output:"]
        for field in required_fields:
            if field not in tc_block:
                return False
            parts = tc_block.split(field, 1)
            if len(parts) < 2 or not parts[1].strip().split('\n', 1)[0].strip():
                if not parts[1].strip():
                    return False
        return True

    @staticmethod
    def validate_test_case_structure(test_case: Dict) -> bool:
        required_fields = ["Test Case ID", "Title", "Description", "Test Steps", "Expected Output"]
        for field in required_fields:
            if field not in test_case or not str(test_case[field]).strip():
                logging.warning(f"Validation failed for TC: Missing or empty field '{field}' in {test_case.get('Test Case ID', 'Unknown ID')}")
                return False

        if "Title" in test_case and test_case["Title"] and len(test_case["Title"].split()) > 20:
            logging.warning(f"Validation failed for TC: Title too long in {test_case.get('Test Case ID', 'Unknown ID')}")
            return False

        if "Test Steps" in test_case and test_case["Test Steps"] and not test_case["Test Steps"].strip().startswith("1."):
            if test_case["Test Steps"].strip():
                 return False

        return True

# Test Case Generator
class TestCaseGenerator:
    def __init__(self, config: ConfigManager):
        self.config = config
        self.examples = self._load_examples()

    def _load_examples(self) -> Dict[str, str]:
        return {
            "Positive": "Test Case ID: TC_001\nTitle: Verify successful login with valid credentials\nDescription: This test checks if a user can log in with correct username and password.\nTest Steps:\n1. Navigate to login page.\n2. Enter valid username.\n3. Enter valid password.\n4. Click login button.\nExpected Output: User is redirected to the dashboard.\nType: Positive",
            "Negative": "Test Case ID: TC_002\nTitle: Verify login failure with invalid password\nDescription: This test checks if login fails with an incorrect password.\nTest Steps:\n1. Navigate to login page.\n2. Enter valid username.\n3. Enter invalid password.\n4. Click login button.\nExpected Output: Error message 'Invalid credentials' is displayed.\nType: Negative",
        }

    def build_prompt(self, text: str, count: int, test_type: str, strategy: str, domain: str) -> str:
        validation_rules = """
        Each test case must strictly follow this structure and rules:
        1. Test Case ID: Must be TC_XXX (e.g., TC_001).
        2. Title: Concise, 5-15 words, describing the main scenario.
        3. Description: Clearly explain the test purpose and context (1-3 sentences).
        4. Test Steps: Numbered (1., 2., 3.), clear, executable actions. Each step on a new line.
        5. Expected Output: Verifiable and specific outcome. Each major outcome on a new line if necessary.
        6. Type: Must be the requested test type ({test_type}).

        DO NOT include any preamble or explanation before the first "Test Case ID:".
        Separate each complete test case block with one empty new line.
        """

        example_case = self.examples.get(test_type, self.examples.get("Positive"))

        return f"""
        As a Senior QA Engineer with 25+ years in {domain} testing, generate exactly {count} {test_type} test cases for the following requirements.
        The testing strategy to consider is {strategy}.
        Requirements: "{text[:Constants.MAX_INPUT_LENGTH]}"

        Follow this exact format for each test case:
        Test Case ID: TC_XXX
        Title: [concise title for the test case]
        Description: [detailed description of what this test case verifies]
        Test Steps:
        1. [First action step]
        2. [Second action step]
        3. [And so on...]
        Expected Output: [Specific expected result after performing the steps]
        Type: {test_type}

        {validation_rules}

        Example of one test case:
        {example_case}

        Begin generating test cases now:
        """

    def generate_with_retry(self, llm: Any, prompt: str, retries: int = 2) -> List[str]:
        for attempt in range(retries + 1):
            try:
                chain = PromptTemplate.from_template("{text}") | llm
                output = chain.invoke({"text": prompt}).strip()
                blocks = [tb.strip() for tb in re.split(r"(?=Test Case ID:)", output) if tb.strip() and "Test Case ID:" in tb]
                valid_blocks = [tb for tb in blocks if TestCaseValidator.is_valid_test_case(tb)]

                if valid_blocks:
                    return valid_blocks
                else:
                    logging.warning(f"Generation attempt {attempt + 1}: No valid blocks found in output. Output was: {output[:300]}...")
            except Exception as e:
                logging.warning(f"Generation attempt {attempt + 1} failed: {e}")
                if attempt < retries:
                    time.sleep(1.5 ** attempt)
        logging.error("All generation retries failed.")
        return []

    def parse_test_cases_for_csv(self, text: str) -> List[Dict]:
        test_cases = []
        blocks = [tb.strip() for tb in re.split(r"(?=Test Case ID:)", text) if tb.strip() and tb.startswith("Test Case ID:")]
        tc_counter = 1

        for block in blocks:
            case = {}
            try:
                id_match = re.search(r"Test Case ID:\s*(TC_\d+)", block)
                
                title_match = re.search(r"Title:\s*(.*?)(?:\nDescription:|\nTest Steps:|\nExpected Output:|\nType:|$)", block, re.DOTALL)
                case["Title"] = title_match.group(1).strip() if title_match else ""

                desc_match = re.search(r"Description:\s*(.*?)(?:\nTest Steps:|\nExpected Output:|\nType:|$)", block, re.DOTALL)
                case["Description"] = desc_match.group(1).strip() if desc_match else ""

                steps_match = re.search(r"Test Steps:\s*((?:.|\n)*?)(?:\nExpected Output:|\nType:|$)", block, re.DOTALL)
                raw_steps_text = steps_match.group(1).strip() if steps_match else ""

                expected_match = re.search(r"Expected Output:\s*(.*?)(?:\nType:|$)", block, re.DOTALL)
                case["Expected Output"] = expected_match.group(1).strip() if expected_match else ""

                type_match = re.search(r"Type:\s*(.*?)(?:\n|$)", block, re.DOTALL)
                case["Type"] = type_match.group(1).strip() if type_match else ""

                if raw_steps_text:
                    individual_steps = [s.strip() for s in raw_steps_text.split('\n') if s.strip()]
                    formatted_steps = []
                    for i, step_text in enumerate(individual_steps):
                        step_text_cleaned = re.sub(r"^\s*\d+\.?\s*-\s*|^\s*\d+\.?\s*|^\s*-\s*", "", step_text).strip()
                        if step_text_cleaned:
                            formatted_steps.append(f"{i + 1}. {step_text_cleaned}")
                    case["Test Steps"] = "\n".join(formatted_steps)
                else:
                    case["Test Steps"] = ""

                case["Test Case ID"] = f"TC_{tc_counter:03d}"

                if case.get("Title") and case.get("Test Steps") and case.get("Expected Output"):
                    if TestCaseValidator.validate_test_case_structure(case):
                        test_cases.append(case)
                        tc_counter += 1
                    else:
                        logging.warning(f"Skipping invalid parsed test case structure: {case.get('Test Case ID')}")
                else:
                    logging.warning(f"Skipping block due to missing critical fields (Title, Steps, Expected): {block[:100]}...")

            except Exception as e:
                logging.error(f"Error parsing block: {block[:100]}... Error: {e}")
                continue
        return test_cases

# Worker for Background Processing
class GenerationWorker(QObject):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, text: str, test_types: List[str], domain: str, count: int, model: str):
        super().__init__()
        self.text = text
        self.test_types = test_types
        self.domain = domain
        self.count = count
        self.model = model
        self.config = ConfigManager()
        self.generator = TestCaseGenerator(self.config)
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            base_url = Constants.DOCKER_OLLAMA_URL if os.environ.get("DOCKER") == "1" else self.config.config.get("ollama_url", Constants.OLLAMA_BASE_URL)
            llm = OllamaLLM(model=self.model, base_url=base_url, temperature=0.0)

            full_output = []
            selected_types = self.test_types

            if not selected_types:
                self.error.emit("No test types selected.")
                return

            total_cases_to_generate = min(self.count, Constants.MAX_TEST_CASES)
            num_selected_types = len(selected_types)
            
            cases_per_type_base = total_cases_to_generate // num_selected_types
            remainder_cases = total_cases_to_generate % num_selected_types
            
            type_counts = {ttype: cases_per_type_base for ttype in selected_types}
            for i in range(remainder_cases):
                type_counts[selected_types[i]] += 1

            processed_types_count = 0
            for test_type, num_cases_for_this_type in type_counts.items():
                if self._is_cancelled:
                    self.error.emit("Generation cancelled by user.")
                    return
                if num_cases_for_this_type == 0:
                    continue

                self.progress.emit(processed_types_count * 100 // num_selected_types, f"Generating {num_cases_for_this_type} {test_type} cases...")
                
                prompt = self.generator.build_prompt(self.text, num_cases_for_this_type, test_type, test_type, self.domain)
                results = self.generator.generate_with_retry(llm, prompt)
                
                if results:
                    type_header = f"--- Test Cases for Type: {test_type.upper()} ---"
                    full_output.append(type_header + "\n" + "\n\n".join(results))
                else:
                    logging.warning(f"No results generated for test type: {test_type}")

                processed_types_count +=1
            
            if self._is_cancelled:
                self.error.emit("Generation cancelled by user.")
                return

            final_text_output = "\n\n".join(full_output)
            if not final_text_output.strip():
                 self.error.emit("No test cases were generated. The model might be unavailable or returned empty results.")
                 return

            self.finished.emit(final_text_output)

        except Exception as e:
            logging.exception("Error during test case generation worker")
            self.error.emit(f"Generation failed: {str(e)}")

# Main Application Window
class TestCaseApp(QWidget):
    def __init__(self):
        super().__init__()
        self.config = ConfigManager()
        self.strategy_checkboxes = {}
        self.setWindowTitle("🤖 Test Case Generator - GenAI")
        self.resize(*self.config.config.get("window_size", [1000, 700]))
        self.setMinimumSize(800, 600)
        self.generated_text = ""
        self.thread = None
        self.worker = None
        self._shutting_down = False

        self.init_ui()
        self.apply_theme(self.config.config.get("ui_theme", "light") == "dark")
        self.refresh_models()

    def init_ui(self):
        self.layout = QVBoxLayout(self)

        # Progress and status
        status_bar_layout = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_bar_layout.addWidget(self.status_label, 1)
        status_bar_layout.addWidget(self.progress, 1)

        # Main content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.layout.addWidget(scroll)

        content_widget = QWidget()
        scroll.setWidget(content_widget)
        content_layout = QVBoxLayout(content_widget)
        content_layout.addLayout(status_bar_layout)

        # Model Selection Group
        model_group = QGroupBox("AI Model Configuration")
        model_group.setObjectName("model_group")  # For styling
        model_layout = QVBoxLayout(model_group)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Select Model:"))
        self.model_selector = QComboBox()
        self.model_selector.setEditable(True)
        self.model_selector.setInsertPolicy(QComboBox.NoInsert)
        completer = QCompleter([], self.model_selector)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.model_selector.setCompleter(completer)
        model_row.addWidget(self.model_selector, 3)

        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(QIcon.fromTheme("view-refresh", QIcon("refresh_icon.png")))
        self.refresh_btn.setToolTip("Refresh available models")
        self.refresh_btn.clicked.connect(self.refresh_models)
        model_row.addWidget(self.refresh_btn)
        model_layout.addLayout(model_row)

        self.model_details = QLabel("No model selected")
        self.model_details.setWordWrap(True)
        self.model_details.setStyleSheet("color: #666666; font-size: 10pt;")
        model_layout.addWidget(self.model_details)
        self.model_selector.currentTextChanged.connect(self.update_model_details)
        content_layout.addWidget(model_group)

        # Domain selection
        domain_layout = QHBoxLayout()
        domain_layout.addWidget(QLabel("Domain:"))
        self.domain = QComboBox()
        self.domain.addItems(Constants.DOMAINS)
        self.domain.setCurrentText(self.config.config.get("default_domain", "Generic"))
        domain_layout.addWidget(self.domain)
        content_layout.addLayout(domain_layout)

        # Test strategy checkboxes
        strategy_group = QGroupBox("Test Types (Select one or more)")
        strategy_group.setObjectName("strategy_group")  # For styling
        strategy_layout_outer = QVBoxLayout(strategy_group)

        checkbox_scroll = QScrollArea()
        checkbox_scroll.setWidgetResizable(True)
        checkbox_scroll.setMinimumHeight(100)
        checkbox_widget = QWidget()
        checkbox_layout = QGridLayout(checkbox_widget)

        cols = 4
        for i, option in enumerate(Constants.TEST_TYPES):
            checkbox = QCheckBox(option)
            if option in ["Positive", "Functional"]:
                checkbox.setChecked(True)
            self.strategy_checkboxes[option] = checkbox
            checkbox_layout.addWidget(checkbox, i // cols, i % cols)

        checkbox_scroll.setWidget(checkbox_widget)
        strategy_layout_outer.addWidget(checkbox_scroll)
        content_layout.addWidget(strategy_group)

        # Test case count
        count_layout = QHBoxLayout()
        count_layout.addWidget(QLabel("Total Test Cases to Generate:"))
        self.total_cases = QSpinBox()
        self.total_cases.setRange(1, Constants.MAX_TEST_CASES)
        self.total_cases.setValue(self.config.config.get("default_test_count", 10))
        count_layout.addWidget(self.total_cases)
        content_layout.addLayout(count_layout)

        # File upload and text input
        input_group = QGroupBox("Requirement Input")
        input_group.setObjectName("input_group")  # For styling
        input_layout = QVBoxLayout(input_group)
        self.upload_btn = QPushButton("Upload Requirement File (.txt, .pdf, .docx)")
        self.upload_btn.clicked.connect(self.upload_file)
        input_layout.addWidget(self.upload_btn)
        input_layout.addWidget(QLabel("Or Paste Requirements Below:"))
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("Enter or paste your requirements here...")
        self.text_input.setMinimumHeight(150)
        input_layout.addWidget(self.text_input)
        content_layout.addWidget(input_group)

        # Action Buttons Layout
        action_buttons_layout = QHBoxLayout()
        self.gen_btn = QPushButton("Generate Test Cases")
        self.gen_btn.clicked.connect(self.generate)
        self.cancel_btn = QPushButton("Cancel Generation")
        self.cancel_btn.setObjectName("cancel_btn")  # For styling
        self.cancel_btn.clicked.connect(self.cancel_generation)
        self.cancel_btn.setEnabled(False)
        action_buttons_layout.addWidget(self.gen_btn)
        action_buttons_layout.addWidget(self.cancel_btn)
        content_layout.addLayout(action_buttons_layout)

        # Results area
        results_group = QGroupBox("Generated Test Cases")
        results_layout = QVBoxLayout(results_group)
        self.result_area = QTextEdit()
        self.result_area.setReadOnly(True)
        self.result_area.setMinimumHeight(200)
        results_layout.addWidget(self.result_area)
        content_layout.addWidget(results_group)

        # Download button
        self.download_btn = QPushButton("Download Test Cases as CSV")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self.download_file)
        content_layout.addWidget(self.download_btn)

        # Developer signature
        signature = QLabel("Developed by Pankaj Chooramani")
        signature.setAlignment(Qt.AlignCenter)
        signature.setStyleSheet("color: #FFD700; font-weight: bold; font-style: italic; padding-top: 10px;")
        content_layout.addWidget(signature)

        # Theme toggle button
        self.theme_toggle_btn = QPushButton("Toggle Dark/Light Theme")
        self.theme_toggle_btn.setObjectName("theme_toggle_btn")  # For styling
        self.theme_toggle_btn.clicked.connect(self.toggle_theme)
        content_layout.addWidget(self.theme_toggle_btn)

        self.setLayout(self.layout)

    def toggle_theme(self):
        current_theme = self.config.config.get("ui_theme", "light")
        new_theme = "dark" if current_theme == "light" else "light"
        self.config.config["ui_theme"] = new_theme
        self.apply_theme(new_theme == "dark")
        self.config.save_config()

    def apply_theme(self, dark_mode: bool):
        palette = QPalette()
        base_style = """
            /* Blue Group Boxes for specific sections */
            #model_group, #strategy_group, #input_group {
                border: 2px solid #4A90E2;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 15px;
            }
            
            #model_group::title, #strategy_group::title, #input_group::title {
                color: #4A90E2;
            }

            /* Green Buttons (except Cancel and Theme Toggle) */
            QPushButton {
                background-color: #4AE24A;
                color: #000000;
                border: 1px solid #3AB83A;
                padding: 8px 15px;
                border-radius: 4px;
                font-weight: bold;
            }
            
            QPushButton:hover {
                background-color: #5AF25A;
            }

            /* Special Buttons */
            #cancel_btn {
                background-color: #D0021B;
                color: white;
                border: 1px solid #B00015;
            }
            
            #cancel_btn:hover {
                background-color: #E0122B;
            }
            
            #theme_toggle_btn {
                background-color: #4A90E2;
                color: white;
                border: 1px solid #3A80D2;
            }
            
            #theme_toggle_btn:hover {
                background-color: #5AA0F2;
            }

            /* Rest of the styling */
            QGroupBox {
                border: 2px solid #666666;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 15px;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
                color: #666666;
            }
            
            QProgressBar {
                border: 1px solid #4A90E2;
                border-radius: 5px;
                text-align: center;
            }
            
            QProgressBar::chunk {
                background-color: #4A90E2;
                border-radius: 3px;
            }
            
            QScrollArea {
                border: 1px solid #4A90E2;
            }
            
            QTextEdit, QComboBox, QSpinBox {
                border: 1px solid #4A90E2;
                padding: 5px;
                border-radius: 3px;
            }
            
            QComboBox::drop-down {
                border: none;
            }
            
            QComboBox::down-arrow {
                image: url(down_arrow.png);
            }
        """

        if dark_mode:
            palette.setColor(QPalette.Window, QColor(30, 30, 40))
            palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
            palette.setColor(QPalette.Base, QColor(40, 40, 50))
            palette.setColor(QPalette.AlternateBase, QColor(50, 50, 60))
            palette.setColor(QPalette.ToolTipBase, QColor(50, 50, 60))
            palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
            palette.setColor(QPalette.Text, QColor(220, 220, 220))
            palette.setColor(QPalette.Button, QColor(70, 80, 90))
            palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
            palette.setColor(QPalette.BrightText, QColor(255, 100, 100))
            palette.setColor(QPalette.Link, QColor(100, 150, 255))
            palette.setColor(QPalette.Highlight, QColor(200, 50, 50))
            palette.setColor(QPalette.HighlightedText, Qt.white)

            specific_style = """
                /* Dark mode adjustments */
                QGroupBox {
                    border-color: #555555;
                }
                
                QGroupBox::title {
                    color: #AAAAAA;
                }
                
                QTextEdit, QComboBox, QSpinBox {
                    background-color: #404050;
                    color: #E0E0E0;
                }
            """
        else:
            palette.setColor(QPalette.Window, QColor(240, 245, 255))
            palette.setColor(QPalette.WindowText, QColor(30, 30, 30))
            palette.setColor(QPalette.Base, Qt.white)
            palette.setColor(QPalette.AlternateBase, QColor(230, 235, 245))
            palette.setColor(QPalette.ToolTipBase, Qt.white)
            palette.setColor(QPalette.ToolTipText, QColor(30, 30, 30))
            palette.setColor(QPalette.Text, QColor(30, 30, 30))
            palette.setColor(QPalette.Button, QColor(208, 2, 27))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.BrightText, Qt.white)
            palette.setColor(QPalette.Link, QColor(0, 100, 200))
            palette.setColor(QPalette.Highlight, QColor(208, 2, 27))
            palette.setColor(QPalette.HighlightedText, Qt.white)

            specific_style = """
                /* Light mode adjustments */
                QTextEdit, QComboBox, QSpinBox {
                    background-color: white;
                    color: #1E1E1E;
                }
            """

        self.setPalette(palette)
        self.setStyleSheet(base_style + specific_style)
        self.update()
        QApplication.processEvents()

    def refresh_models(self):
        try:
            self.status_label.setText("Fetching available models...")
            self.refresh_btn.setEnabled(False)
            QApplication.processEvents()

            current_model = self.model_selector.currentText()
            self.model_selector.clear()
            available_models = ModelService.get_available_models()

            if not available_models or (len(available_models) == 1 and available_models[0] == Constants.DEFAULT_MODEL and not os.environ.get("DOCKER") == "1"):
                self.model_selector.addItem(Constants.DEFAULT_MODEL)
                self.model_selector.setCurrentText(Constants.DEFAULT_MODEL)
                raise ValueError("No models found or Ollama might not be running/accessible.")

            self.model_selector.addItems(available_models)
            if self.model_selector.completer():
                self.model_selector.completer().setModel(self.model_selector.model())

            if current_model and current_model in available_models:
                self.model_selector.setCurrentText(current_model)
            elif Constants.DEFAULT_MODEL in available_models:
                self.model_selector.setCurrentText(Constants.DEFAULT_MODEL)
            elif available_models:
                self.model_selector.setCurrentIndex(0)

            self.status_label.setText(f"Found {len(available_models)} models. Ready.")
            self.update_model_details(self.model_selector.currentText())

        except Exception as e:
            self.status_label.setText(f"Error fetching models: {str(e)}")
            QMessageBox.warning(self, "Model Refresh Error",
                                f"Failed to fetch models: {str(e)}\n\n"
                                "Please ensure:\n"
                                "1. Ollama is installed and running.\n"
                                "2. The Ollama server is accessible (check URL in config or constants).\n"
                                "3. You have downloaded at least one model via Ollama (e.g., 'ollama pull llama3.2').\n"
                                f"Current Ollama URL: {self.config.config.get('ollama_url', Constants.OLLAMA_BASE_URL)}")
            if not self.model_selector.count():
                self.model_selector.addItem(Constants.DEFAULT_MODEL)
                self.model_selector.setCurrentText(Constants.DEFAULT_MODEL)
        finally:
            self.refresh_btn.setEnabled(True)

    def update_model_details(self, model_name: str):
        if model_name:
            self.model_details.setText(f"Selected Model: {model_name}\nThis model will be used for test case generation.")
        else:
            self.model_details.setText("No model selected. Please select or refresh models.")

    def upload_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Requirement File",
            self.config.config.get("recent_files", [os.path.expanduser("~")])[0] if self.config.config.get("recent_files") else os.path.expanduser("~"),
            "Requirement Files (*.txt *.pdf *.docx);;All Files (*)"
        )

        if file_path:
            try:
                self.status_label.setText(f"Loading file: {os.path.basename(file_path)}...")
                QApplication.processEvents()
                content = FileService.extract_text(file_path)
                self.text_input.setPlainText(content)
                self.config.add_recent_file(os.path.dirname(file_path))
                self.status_label.setText(f"Loaded: {os.path.basename(file_path)}")
            except Exception as e:
                self.status_label.setText("Error loading file.")
                QMessageBox.critical(self, "Error Reading File", f"Could not read or parse file: {str(e)}")

    def generate(self):
        # Check if we're already generating
        if self.thread and self.thread.isRunning():
            QMessageBox.information(self, "Generation in Progress", 
                                  "Test case generation is already in progress.")
            return

        # Clear previous results
        self.generated_text = ""
        self.result_area.clear()
        self.status_label.setText("🟡 Starting test case generation...")
        self.progress.setVisible(True)
        self.progress.setRange(0,100)
        self.progress.setValue(0)
        self.gen_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.download_btn.setEnabled(False)

        selected_model = self.model_selector.currentText()
        if not selected_model:
            QMessageBox.warning(self, "No Model", "Please select an AI model first.")
            self.status_label.setText("⚠️ No model selected.")
            self.progress.setVisible(False)
            self.gen_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            return

        selected_types = [t for t, cb in self.strategy_checkboxes.items() if cb.isChecked()]
        domain = self.domain.currentText()
        count = self.total_cases.value()
        text = self.text_input.toPlainText().strip()

        if not selected_types:
            self.status_label.setText("⚠️ No test types selected.")
            self.progress.setVisible(False)
            QMessageBox.warning(self, "Input Error", "Please select at least one test type.")
            self.gen_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            return

        if not text:
            self.status_label.setText("⚠️ No requirement input provided.")
            self.progress.setVisible(False)
            QMessageBox.warning(self, "Input Error", "Please provide requirement text or upload a file.")
            self.gen_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            return

        # Clean up old thread if it exists
        if self.thread:
            self.thread.quit()
            self.thread.wait(1000)
            self.thread.deleteLater()
            self.thread = None

        # Create new thread and worker
        self.thread = QThread()
        self.worker = GenerationWorker(text, selected_types, domain, count, selected_model)
        self.worker.moveToThread(self.thread)

        # Connect signals
        self.worker.finished.connect(self.on_generation_complete)
        self.worker.error.connect(self.on_generation_error)
        self.worker.progress.connect(self.update_progress)
        self.thread.started.connect(self.worker.run)

        # Cleanup connections
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self._on_thread_finished)

        try:
            self.thread.start()
        except Exception as e:
            self.status_label.setText(f"⚠️ Failed to start thread: {str(e)}")
            self.thread.deleteLater()
            self.thread = None
            self.worker = None
            self.gen_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)

    def _on_thread_finished(self):
        if not self._shutting_down:
            self.gen_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
        
        # Clean up thread and worker
        if self.thread:
            self.thread.deleteLater()
            self.thread = None
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def cancel_generation(self):
        if self.worker and self.thread and self.thread.isRunning():
            self.status_label.setText("🟡 Cancelling generation...")
            self.cancel_btn.setEnabled(False)
            self.worker.cancel()
            
            if not self.thread.wait(2000):
                self.thread.terminate()
                self.thread.wait()

    def update_progress(self, value: int, message: str):
        self.progress.setValue(value)
        self.status_label.setText(f"⏳ {message}")

    def on_generation_complete(self, result: str):
        if self.worker and self.worker._is_cancelled:
             self.status_label.setText("⏹️ Test case generation was cancelled.")
             self.result_area.setPlainText("Generation cancelled by user.")
        else:
            self.progress.setValue(100)
            self.status_label.setText("✅ Test case generation completed successfully!")
            self.result_area.setPlainText(result)
            self.generated_text = result
            if result.strip():
                self.download_btn.setEnabled(True)
            else:
                self.status_label.setText("ℹ️ Generation completed, but no test cases were produced.")
                self.download_btn.setEnabled(False)

        self.progress.setVisible(False)
        self._on_thread_finished()

    def on_generation_error(self, error_msg: str):
        self.status_label.setText(f"⚠️ Error: {error_msg}")
        self.result_area.setPlainText(f"An error occurred:\n{error_msg}")
        QMessageBox.warning(self, "Generation Error", error_msg)
        self.progress.setVisible(False)
        self.download_btn.setEnabled(False)
        self._on_thread_finished()

    def download_file(self):
        if not self.generated_text.strip():
            QMessageBox.warning(self, "No Data", "No test cases have been generated to download.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        domain_prefix = self.domain.currentText().replace(" ", "_") if self.domain.currentText() != "Generic" else "tests"
        default_filename = f"{domain_prefix}_test_cases_{timestamp}.csv"
        
        save_dir = self.config.config.get("last_save_dir", os.path.expanduser("~/Downloads"))
        default_path = os.path.join(save_dir, default_filename)

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Test Cases",
            default_path,
            "CSV Files (*.csv);;All Files (*)"
        )

        if file_path:
            try:
                test_cases_for_csv = TestCaseGenerator(self.config).parse_test_cases_for_csv(self.generated_text)

                if not test_cases_for_csv:
                    QMessageBox.warning(self, "Warning", "No valid test cases could be parsed for export.")
                    return

                fieldnames = ["Test Case ID", "Title", "Description", "Test Steps", "Expected Output", "Type"]

                with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
                    writer.writeheader()

                    for case_dict in test_cases_for_csv:
                        row_to_write = {}
                        for field in fieldnames:
                            value = case_dict.get(field, "")
                            if isinstance(value, str):
                                row_to_write[field] = value.strip()
                            else:
                                row_to_write[field] = str(value).strip()
                        writer.writerow(row_to_write)
                
                self.config.config["last_save_dir"] = os.path.dirname(file_path)
                self.config.save_config()
                QMessageBox.information(self, "Save Successful", f"Test cases saved to:\n{file_path}")
                self.status_label.setText(f"✅ Test cases saved to {os.path.basename(file_path)}")

            except Exception as e:
                logging.exception("Error during CSV file download/write")
                QMessageBox.critical(self, "Save Error", f"Could not save file: {str(e)}")
                self.status_label.setText("⚠️ Error saving file.")

    def closeEvent(self, event):
        self._shutting_down = True
        
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(
                self, 'Confirm Exit',
                "Test case generation is in progress. Are you sure you want to exit?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            
            if reply == QMessageBox.Yes:
                if self.worker:
                    self.worker.cancel()
                if not self.thread.wait(Constants.THREAD_SHUTDOWN_TIMEOUT):
                    self.thread.terminate()
                    self.thread.wait()
            else:
                event.ignore()
                self._shutting_down = False
                return

        # Save configuration
        self.config.config["window_size"] = [self.width(), self.height()]
        self.config.config["default_model"] = self.model_selector.currentText()
        self.config.config["default_domain"] = self.domain.currentText()
        self.config.config["default_test_count"] = self.total_cases.value()
        self.config.config["ui_theme"] = "dark" if self.palette().window().color().lightness() < 128 else "light"
        self.config.save_config()
        
        event.accept()

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
    
    if os.environ.get("DOCKER") == "1":
        logging.info("Running in Docker environment. Ollama URL set to internal Docker host.")
        Constants.OLLAMA_BASE_URL = Constants.DOCKER_OLLAMA_URL

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    ex = TestCaseApp()
    ex.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()