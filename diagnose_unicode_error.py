# Diagnostic Script: Find Unicode Escape Errors
# Run this to identify the source of "unsupported Unicode escape sequence" error

"""
This script helps diagnose Python Unicode escape sequence errors.

Common causes:
1. Windows paths in .env file: C:\Users\... → Must be C:\\Users\\ or C:/Users/
2. JSON strings with unescaped backslashes
3. Regex patterns without raw string prefix
"""

import os
import sys
import json
from pathlib import Path

def check_env_file(env_path=".env"):
    """Check .env file for problematic backslashes."""
    print(f"\n🔍 Checking {env_path}...")
    
    if not Path(env_path).exists():
        print(f"   ❌ {env_path} not found")
        return
    
    issues = []
    with open(env_path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Check for Windows paths
            if ':\\' in line and not ':\\\\' in line:
                issues.append({
                    'line': i,
                    'content': line,
                    'issue': 'Windows path with single backslash',
                    'fix': line.replace(':\\', ':\\\\')
                })
            
            # Check for common escape sequences that might be errors
            if '\\u' in line or '\\t' in line or '\\n' in line:
                # Check if it's in a quoted value
                if '=' in line:
                    key, value = line.split('=', 1)
                    if value.strip() and value.strip()[0] not in ('"', "'"):
                        issues.append({
                            'line': i,
                            'content': line,
                            'issue': 'Possible unescaped backslash in value',
                            'fix': f'{key}="{value}"  # Or escape backslashes: {value.replace(chr(92), chr(92)*2)}'
                        })
    
    if issues:
        print(f"   ⚠️ Found {len(issues)} potential issues:")
        for issue in issues:
            print(f"\n   Line {issue['line']}: {issue['issue']}")
            print(f"   Current: {issue['content']}")
            print(f"   Fix:     {issue['fix']}")
    else:
        print("   ✅ No obvious issues found")
    
    return issues

def check_json_files():
    """Check JSON files for unescaped backslashes."""
    print("\n🔍 Checking JSON files...")
    
    json_files = list(Path('.').rglob('*.json'))
    issues = []
    
    for json_file in json_files:
        if 'node_modules' in str(json_file) or '.git' in str(json_file):
            continue
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                content = f.read()
                # Try to parse
                json.loads(content)
        except json.JSONDecodeError as e:
            if 'escape' in str(e).lower():
                issues.append({
                    'file': str(json_file),
                    'error': str(e)
                })
        except Exception as e:
            pass
    
    if issues:
        print(f"   ⚠️ Found {len(issues)} JSON files with escape errors:")
        for issue in issues:
            print(f"\n   File: {issue['file']}")
            print(f"   Error: {issue['error']}")
    else:
        print("   ✅ All JSON files valid")
    
    return issues

def check_environment_variables():
    """Check current environment variables for problematic paths."""
    print("\n🔍 Checking environment variables...")
    
    issues = []
    for key, value in os.environ.items():
        if key.startswith(('GOOGLE', 'OPENAI', 'TAVILY', 'S2', 'POSTGRES', 'REDIS', 'QDRANT', 
                          'CORPUS', 'API', 'AGENT', 'VITE')):
            # Check for Windows paths
            if ':\\' in value and ':\\\\' not in value:
                issues.append({
                    'key': key,
                    'value': value,
                    'issue': 'Windows path with single backslash',
                    'fix': value.replace(':\\', ':\\\\')
                })
    
    if issues:
        print(f"   ⚠️ Found {len(issues)} problematic environment variables:")
        for issue in issues:
            print(f"\n   {issue['key']}")
            print(f"   Current: {issue['value']}")
            print(f"   Fix:     {issue['fix']}")
    else:
        print("   ✅ No obvious issues in environment variables")
    
    return issues

def test_python_string_parsing():
    """Test if Python can parse common strings."""
    print("\n🔍 Testing Python string parsing...")
    
    test_strings = [
        ("Windows path", r"C:\Users\Admin"),
        ("Forward slash", "C:/Users/Admin"),
        ("Escaped backslash", "C:\\\\Users\\\\Admin"),
        ("Newline escape", "test\\nstring"),
        ("Tab escape", "test\\tstring"),
    ]
    
    for name, test_str in test_strings:
        try:
            # Try to parse as if it came from JSON or config
            eval(f'"{test_str}"')
            print(f"   ✅ {name}: OK")
        except SyntaxError as e:
            print(f"   ❌ {name}: FAIL - {e}")

if __name__ == "__main__":
    print("=" * 60)
    print("UNICODE ESCAPE SEQUENCE DIAGNOSTIC")
    print("=" * 60)
    
    env_issues = check_env_file(".env")
    json_issues = check_json_files()
    env_var_issues = check_environment_variables()
    test_python_string_parsing()
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    total_issues = len(env_issues or []) + len(json_issues or []) + len(env_var_issues or [])
    
    if total_issues == 0:
        print("✅ No obvious issues found!")
        print("\nIf you're still getting the error, it might be:")
        print("1. Coming from the API/UI layer (check NestJS logs)")
        print("2. In a dynamically constructed string")
        print("3. In Windows-specific file paths being passed at runtime")
    else:
        print(f"⚠️ Found {total_issues} potential issues")
        print("\nMost common fixes:")
        print("1. In .env: C:\\Users\\... → C:\\\\Users\\\\ or C:/Users/")
        print("2. Use forward slashes instead: C:/Users/Admin")
        print("3. Use raw strings in Python: r'C:\\Users\\Admin'")
    
    print("\n" + "=" * 60)
