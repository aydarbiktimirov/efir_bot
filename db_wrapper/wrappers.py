#!/usr/bin/env python

import datetime
import pymongo
import collections

_client = None


def init(hostname, port, username, password, **kwargs):
    global _client
    _client = pymongo.MongoClient(hostname, port)
    if username is not None and password is not None:
        _client.zefir.authenticate(username, password)


class Chat(object):
    def __init__(self, chat_id):
        self.chat_id = chat_id
        rec = _client.zefir.chats.find_one({'chat_id': self.chat_id}) or {}
        self.users = rec.get('users', [])

    def add_user(self, telegram_id):
        if telegram_id not in self.users:
            self.users.append(telegram_id)
        _client.zefir.chats.update_one({'chat_id': self.chat_id}, {'$addToSet': {'users': telegram_id}}, True)


class Team(object):
    def __init__(self, name, flag):
        self.name = name
        self.flag = flag

class User(object):
    def __init__(self, telegram_id):
        self.telegram_id = telegram_id
        rec = _client.zefir.users.find_one({'telegram_id': self.telegram_id}) or {}
        self.rating = rec.get('rating', 0)
        self.name = rec.get('name', {'first_name': '', 'last_name': ''})
        for k in ['first_name', 'last_name']:
            if self.name[k] is None:
                self.name[k] = ''
        self.prev_rating = rec.get('prev_rating', 0)

    def update_rating(self, new_rating):
        _client.zefir.users.update_one({'telegram_id': self.telegram_id}, {'$set': {'rating': new_rating, 'prev_rating': self.rating}}, True)
        self.prev_rating = self.rating
        self.rating = new_rating

    def get_votes(self):
        return [Vote(rec['user_id'], rec['event_id'])
                for rec in _client.zefir.votes.find({'$query': {'user_id': self.telegram_id}, '$orderby': {'timestamp': -1}})]

    def get_last_vote_for_finished_event(self):
        votes = self.get_votes()
        for vote in votes:
            if Event(vote.event_id).processed:
                return vote
        pass

    def get_leaderbord_index(self):
        num_before = _client.zefir.users.count({'rating': {'$gt': self.rating}})
        return num_before + 1

    @staticmethod
    def ensure_exists(telegram_id, name):
        _client.zefir.users.update_one({'telegram_id': telegram_id}, {'$set': {'name': name}}, True)

    @staticmethod
    def count():
        return _client.zefir.users.count()

    @staticmethod
    def get_top(n):
        return [User(rec['telegram_id'])
                for rec in _client.zefir.users.aggregate([{'$sort': {'rating': -1}}, {'$limit': n}])]


class Event(object):
    def __init__(self, event_id):
        self.event_id = event_id
        rec = _client.zefir.events.find_one({'event_id': self.event_id}) or {}
        self.score = rec.get('score')
        self.vote_until = rec.get('vote_until')
        self.name = rec.get('name', '')
        self.processed = rec.get('processed', False)
        self.start_notification_sent = rec.get('start_notification_sent', False)
        self.score_notification_sent = rec.get('score_notification_sent', False)

        teams = rec.get('teams', [{'flag': '', 'name': 'Team 1'}, {'flag': '', 'name': 'Team 2'}])
        self.team1 = Team(teams[0]['name'], teams[0]['flag'])
        self.team2 = Team(teams[1]['name'], teams[1]['flag'])

    def set_score(self, new_score):
        _client.zefir.events.update_one({'event_id': self.event_id}, {'$set': {'score': new_score}}, True)

    def set_processed(self):
        _client.zefir.events.update_one({'event_id': self.event_id}, {'$set': {'processed': True}}, True)

    def set_start_notification_sent(self):
        _client.zefir.events.update_one({'event_id': self.event_id}, {'$set': {'start_notification_sent': True}}, True)

    def set_score_notification_sent(self):
        _client.zefir.events.update_one({'event_id': self.event_id}, {'$set': {'score_notification_sent': True}}, True)

    def get_votes(self):
        return [Vote(rec['user_id'], self.event_id) for rec in _client.zefir.votes.find({'event_id': self.event_id})]

    def get_vote_stats(self):
        votes = self.get_votes()
        prediction_counter = collections.Counter([v.predicted_score for v in votes])
        total = len(votes)
        return dict([(k, 1.0 * v / total) for k, v in prediction_counter.iteritems()])

    def add_listener_chat(self, chat):
        _client.zefir.events.update({'event_id': self.event_id}, {'$addToSet': {'listeners': chat}})

    def get_listeners(self):
        return [chat for chat in _client.zefir.events.find_one({'event_id': self.event_id}).get('listeners', [])]

    @staticmethod
    def get_last_processed_event():
        event = _client.zefir.events.find_one({'$query': {'processed': True}, '$orderby': {'vote_until': -1}})
        return Event(event['event_id']) if event is not None else None

    @staticmethod
    def add(event_id, name, teams, vote_until):
        _client.zefir.events.update_one({'event_id': event_id}, {'$set': {'name': name, 'teams': teams, 'vote_until': vote_until}}, True)

    @staticmethod
    def get_events_with_no_start_notification():
        current_time = datetime.datetime.utcnow()
        return [Event(rec['event_id'])
                for rec in _client.zefir.events.find({'start_notification_sent': {'$exists': False}, 'vote_until': {'$lt': current_time}})]

    @staticmethod
    def get_events_with_no_score_notification():
        return [Event(rec['event_id'])
                for rec in _client.zefir.events.find({'score_notification_sent': {'$exists': False}, 'processed': {'$exists': True}})]

    @staticmethod
    def get_unprocessed_events():
        return [Event(rec['event_id'])
                for rec in _client.zefir.events.find({'processed': {'$exists': False}, 'score': {'$exists': True}})]

    @staticmethod
    def get_upcoming_events(limit = 1):
        current_time = datetime.datetime.utcnow()
        query = {'vote_until': {'$gt': current_time}}
        return [Event(rec['event_id'])
                for rec in _client.zefir.events.find({'$query': query, '$orderby': {'vote_until': 1}}, limit=limit)]

    @staticmethod
    def get_all():
        return [Event(rec['event_id'])
                for rec in _client.zefir.events.find({})]


class Vote(object):
    def __init__(self, user_id, event_id):
        self.user_id = user_id
        self.event_id = event_id
        rec = _client.zefir.votes.find_one({'user_id': self.user_id, 'event_id': self.event_id}) or {}
        self.predicted_score = rec.get('predicted_score')
        self.timestamp = rec.get('timestamp')

    def set_score(self, new_score):
        if self.predicted_score is None:
            _client.zefir.votes.update({'user_id': self.user_id, 'event_id': self.event_id}, {'$set': {'predicted_score': new_score, 'timestamp': datetime.datetime.now()}}, True)
