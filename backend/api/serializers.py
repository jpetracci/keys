from django.contrib.auth.models import User
from rest_framework import serializers
from .models import Transaction

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'password']
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user
    
class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            'id',
            'trans_description',
            'trans_category',
            'trans_date',
            'trans_amount',
            'account_name',
            'source_file',
            'author',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['author', 'source_file', 'created_at', 'updated_at']
