#!/usr/bin/env python3
"""
Quick test script to verify Semantic Scholar API integration locally.
Run with: python test_scholar_local.py
"""

import os
import sys
from pathlib import Path

# Add apps/agent to path
sys.path.insert(0, str(Path(__file__).parent / "apps" / "agent"))

from app.graph.nodes.scholar import _scholar_search

def main():
    # Set environment variables if not already set
    if not os.getenv("S2_API_KEY"):
        print("⚠️  WARNING: S2_API_KEY not set in environment!")
        print("Set it with: $env:S2_API_KEY='your_key_here'")
        return
    
    # Test query
    test_query = "machine learning transformers attention mechanism"
    
    print(f"🔍 Testing Scholar search with query: {test_query}")
    print("-" * 60)
    
    try:
        results, calls = _scholar_search(test_query)
        
        print(f"\n✅ Success!")
        print(f"📊 Results: {len(results)} papers")
        print(f"🌐 API calls made: {calls}")
        print("\n📄 Sample results:")
        
        for i, paper in enumerate(results[:3], 1):
            print(f"\n{i}. {paper.get('title', 'No title')[:80]}")
            print(f"   URL: {paper.get('url', 'N/A')}")
            print(f"   Type: {paper.get('publication_type', 'N/A')}")
            print(f"   Tier: {paper.get('tier', 'N/A')}")
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
