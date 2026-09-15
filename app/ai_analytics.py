from collections import Counter
from datetime import datetime, timedelta
try:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except Exception:
    HAS_SKLEARN = False
import numpy as np
from app.models import AccessLog, BtsSite, Technician


def get_face_accuracy_distribution():
    logs = AccessLog.query.filter(
        AccessLog.face_distance.isnot(None),
        AccessLog.face_distance >= 0,
        AccessLog.face_distance <= 1
    ).all()
    if not logs:
        return {'buckets': [], 'confidence_rate': 0.0}
    distances = [log.face_distance for log in logs]
    buckets = []
    labels = ['Sangat Yakin (0-0.3)', 'Yakin (0.3-0.5)', 'Biasa (0.5-0.7)', 'Rendah (0.7-1.0)']
    ranges = [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0)]
    for i, (lo, hi) in enumerate(ranges):
        count = sum(1 for d in distances if lo <= d < hi)
        buckets.append({
            'label': labels[i],
            'count': count,
            'percentage': round(count / len(distances) * 100, 1) if distances else 0
        })
    successful = sum(1 for log in logs if log.face_result == 'SUCCESS')
    confidence_rate = round(successful / len(logs) * 100, 1) if logs else 0.0
    return {'buckets': buckets, 'confidence_rate': confidence_rate, 'total_samples': len(logs)}


def detect_access_anomalies():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = today_start - timedelta(days=7)
    logs = AccessLog.query.filter(AccessLog.timestamp >= seven_days_ago).all()
    if not logs:
        return {'anomalies': [], 'message': 'Tidak cukup data'}
    daily_counts = {}
    for log in logs:
        day = log.timestamp.date().isoformat()
        daily_counts[day] = daily_counts.get(day, 0) + 1
    counts = np.array(list(daily_counts.values())).reshape(-1, 1)
    if len(counts) < 3:
        return {'anomalies': [], 'message': 'Data terbatas untuk deteksi anomali'}
    anomalies = []
    days = list(daily_counts.keys())
    if HAS_SKLEARN:
        scaler = StandardScaler()
        scaled = scaler.fit_transform(counts)
        iso_forest = IsolationForest(contamination=0.15, random_state=42)
        labels = iso_forest.fit_predict(scaled)
        for i, label in enumerate(labels):
            if label == -1:
                anomalies.append({
                    'date': days[i],
                    'count': daily_counts[days[i]],
                    'type': 'akses_tidak_biasa'
                })
    else:
        values = np.array(list(daily_counts.values()), dtype=float)
        mean_val = np.mean(values)
        std_val = np.std(values)
        if std_val > 0:
            for i, val in enumerate(values):
                if abs(val - mean_val) > 1.96 * std_val:
                    anomalies.append({
                        'date': days[i],
                        'count': int(val),
                        'type': 'akses_tidak_biasa'
                    })
    return {'anomalies': anomalies, 'daily_counts': dict(zip(days, list(daily_counts.values()))), 'message': 'OK'}


def predict_threat_scores(sites):
    scores = {}
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    for site in sites:
        today_logs = AccessLog.query.filter_by(site_id=site.site_id).filter(
            AccessLog.timestamp >= today_start
        ).all()
        denied = sum(1 for log in today_logs if log.access_result == 'DENIED')
        incidents = sum(1 for log in today_logs if log.incident_level in ('WARNING', 'HIGH', 'CRITICAL'))
        risk_score = min(100, (denied * 10 + incidents * 15))
        scores[site.site_id] = {
            'site_name': site.site_name,
            'score': risk_score,
            'denied': denied,
            'incidents': incidents,
            'risk_level': 'CRITICAL' if risk_score >= 70 else 'HIGH' if risk_score >= 40 else 'WARNING' if risk_score >= 15 else 'NORMAL'
        }
    return scores


def get_hourly_access_pattern():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = today_start - timedelta(days=7)
    logs = AccessLog.query.filter(AccessLog.timestamp >= seven_days_ago).all()
    hourly = Counter()
    for log in logs:
        if log.timestamp:
            hourly[log.timestamp.hour] += 1
    pattern = {}
    for hour in range(24):
        pattern[hour] = hourly.get(hour, 0)
    return pattern


def get_denial_reasons():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    logs = AccessLog.query.filter_by(access_result='DENIED').filter(
        AccessLog.timestamp >= today_start
    ).all()
    reason_counts = Counter()
    for log in logs:
        reason = log.reason_code or 'UNKNOWN'
        reason_counts[reason] += 1
    top_reasons = [{'reason': r, 'count': c} for r, c in reason_counts.most_common(5)]
    return top_reasons


def get_technician_risk_profile():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    technicians = Technician.query.all()
    profiles = []
    for tech in technicians:
        logs = AccessLog.query.filter_by(technician_id=tech.technician_id).filter(
            AccessLog.timestamp >= today_start
        ).all()
        denied = sum(1 for log in logs if log.access_result == 'DENIED')
        total = len(logs)
        denial_rate = round(denied / total * 100, 1) if total > 0 else 0.0
        profiles.append({
            'technician_id': tech.technician_id,
            'name': tech.name,
            'total_access': total,
            'denied': denied,
            'denial_rate': denial_rate,
            'risk_flag': 'HIGH' if denial_rate > 50 else 'MEDIUM' if denial_rate > 20 else 'LOW'
        })
    return sorted(profiles, key=lambda x: x['denial_rate'], reverse=True)[:10]


def generate_ai_insights():
    insights = []
    face_data = get_face_accuracy_distribution()
    if face_data['confidence_rate'] < 80:
        insights.append({
            'type': 'WARNING',
            'title': 'Akurasi Face Recognition Menurun',
            'description': f'Accuracy hanya {face_data["confidence_rate"]}% dari {face_data["total_samples"]} sampel',
            'recommendation': 'Periksa kamera dan pencahayaan'
        })
    else:
        insights.append({
            'type': 'SUCCESS',
            'title': 'Face Recognition Optimo',
            'description': f'Accuracy {face_data["confidence_rate"]}% dari {face_data["total_samples"]} sampel',
            'recommendation': 'Sistem berjalan normal'
        })
    site_scores = predict_threat_scores(BtsSite.query.all())
    critical_sites = [s for s in site_scores.values() if s['risk_level'] in ('HIGH', 'CRITICAL')]
    if critical_sites:
        critical_names = ', '.join([s['site_name'] for s in list(critical_sites)[:3]])
        insights.append({
            'type': 'DANGER',
            'title': 'Site dengan Risiko Tinggi',
            'description': f'{len(critical_sites)} site butuh perhatian: {critical_names}',
            'recommendation': 'Dispatche security patrol'
        })
    anomalies = detect_access_anomalies()
    if anomalies['anomalies']:
        insights.append({
            'type': 'WARNING',
            'title': 'Pola Akses Anomali Terdeteksi',
            'description': f'{len(anomalies["anomalies"])} anomali terdeteksi dalam 7 hari terakhir',
            'recommendation': 'Audit ulang prosedur akses'
        })
    reasons = get_denial_reasons()
    if reasons:
        top_reason = reasons[0]
        insights.append({
            'type': 'INFO',
            'title': 'Penyebab Penolakan Terbanyak',
            'description': f'"{top_reason["reason"]}" - {top_reason["count"]} kali penolakan',
            'recommendation': f'Fokus pada solusi: {top_reason["reason"]}'
        })
    return insights


def get_site_heatmap_data():
    sites = BtsSite.query.all()
    today_start = datetime.utcnow().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    heatmap = []
    for site in sites:
        today_logs = AccessLog.query.filter_by(
            site_id=site.site_id
        ).filter(AccessLog.timestamp >= today_start).all()
        denied = sum(1 for log in today_logs if log.access_result == 'DENIED')
        incidents = sum(1 for log in today_logs if log.incident_level in ('WARNING', 'HIGH', 'CRITICAL'))
        risk_score = min(100, (denied * 10 + incidents * 15))
        if risk_score >= 70:
            risk_level = 'CRITICAL'
        elif risk_score >= 40:
            risk_level = 'HIGH'
        elif risk_score >= 15:
            risk_level = 'WARNING'
        else:
            risk_level = 'NORMAL'
        try:
            lat = float(site.latitude) if site.latitude else 0
            lon = float(site.longitude) if site.longitude else 0
        except (ValueError, TypeError):
            lat, lon = 0, 0
        heatmap.append({
            'site_id': site.site_id,
            'site_name': site.site_name,
            'latitude': lat,
            'longitude': lon,
            'risk_score': risk_score,
            'risk_level': risk_level,
            'denied_count': denied,
            'incident_count': incidents,
            'location': site.location
        })
    return heatmap
