import os
import sys
sys.path.insert(0, r'D:\EPM研发标杆\github\github-auto-harness-fixer')
from src.business_module.issue_parser import IssueParser

# Test issue parsing with a realistic issue
issue = {
    'number': 86887,
    'title': 'TypeError: Cannot read properties of undefined (reading session)',
    'body': '## Bug Report\n\n**Error:**\n```\nTypeError: Cannot read properties of undefined (reading session)\n    at getSession (src/lib/auth.ts:45:23)\n    at authenticate (src/middleware/auth.ts:12:5)\n```\n\n**Expected:** Should handle missing session gracefully\n**Actual:** Crashes with TypeError\n',
    'labels': ['bug'],
    'state': 'open',
    'created_at': '2024-01-01',
    'html_url': 'https://github.com/test/test/issues/86887'
}

repo_path = r'D:\EPM研发标杆\github\github-auto-harness-fixer'
parsed = IssueParser.parse(issue, repo_path)
print(f'Parsed issue:')
print(f'  is_bug: {parsed["is_bug"]}')
print(f'  error_type: {parsed["error_type"]}')
print(f'  language: {parsed["language"]}')
print(f'  file_refs: {parsed["file_refs"]}')
print(f'  stack_trace_files: {parsed["stack_trace_files"]}')
print(f'  affected_functions: {parsed["affected_functions"]}')
print(f'  error_context: {parsed["error_context"]}')
