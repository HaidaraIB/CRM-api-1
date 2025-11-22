"""
Script to fix duplicate campaign codes in the database.
Run this script using: python manage.py shell < fix_duplicate_campaigns.py
Or run: python manage.py shell and paste the code.
"""

from crm.models import Campaign
from django.db.models import Count

# Find all duplicate codes
duplicates = Campaign.objects.values('code').annotate(count=Count('code')).filter(count__gt=1)

print(f"Found {len(duplicates)} duplicate codes:")
for dup in duplicates:
    print(f"  Code: {dup['code']}, Count: {dup['count']}")

# Fix duplicates by assigning new unique codes
for dup in duplicates:
    code = dup['code']
    campaigns = Campaign.objects.filter(code=code).order_by('id')
    
    # Keep the first one, fix the rest
    first_campaign = campaigns.first()
    print(f"\nFixing duplicates for code: {code}")
    print(f"  Keeping campaign ID {first_campaign.id} ({first_campaign.name})")
    
    # Fix the rest
    for idx, campaign in enumerate(campaigns[1:], start=1):
        # Generate new code
        if code.startswith('CAMP'):
            try:
                base_num = int(code.replace('CAMP', ''))
                new_num = base_num + (idx * 1000)  # Add large offset to avoid conflicts
            except ValueError:
                new_num = 1000 + idx
        else:
            new_num = 1000 + idx
        
        new_code = f"CAMP{str(new_num).zfill(3)}"
        
        # Make sure it's unique
        counter = 1
        while Campaign.objects.filter(code=new_code).exists():
            new_num = base_num + (idx * 1000) + counter
            new_code = f"CAMP{str(new_num).zfill(3)}"
            counter += 1
            if counter > 100:
                import time
                new_code = f"CAMP{str(int(time.time()) + idx)[-6:]}"
                break
        
        campaign.code = new_code
        campaign.save()
        print(f"  Fixed campaign ID {campaign.id} ({campaign.name}) -> {new_code}")

print("\nDone fixing duplicate codes!")

