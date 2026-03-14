pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py collectstatic --noinput
crontab crontab_complete.txt
sudo systemctl restart crm-api