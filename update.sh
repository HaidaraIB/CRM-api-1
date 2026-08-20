pip install -r requirements.txt
python manage.py makemigrations
python manage.py migrate
python manage.py collectstatic --noinput
crontab crontab_complete.txt
sudo systemctl restart crm-api
sudo systemctl restart crm-qcluster
systemctl is-active --quiet crm-api     && echo "crm-api: running"
systemctl is-active --quiet crm-qcluster && echo "crm-qcluster: running"
