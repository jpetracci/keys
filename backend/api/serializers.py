from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from .models import Transaction

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'password']
        extra_kwargs = {'password': {'write_only': True}}

    def validate(self, attrs):
        # create_user() stores whatever it is given; AUTH_PASSWORD_VALIDATORS
        # only run when asked. The unsaved user lets the similarity validator
        # compare the password with the username.
        if 'password' in attrs:
            try:
                validate_password(attrs['password'], user=User(username=attrs.get('username', '')))
            except DjangoValidationError as exc:
                raise serializers.ValidationError({'password': exc.messages}) from exc
        return attrs

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

class TransactionFilterSerializer(serializers.Serializer):
    """Query parameters shared by the transaction list and summary endpoints."""

    category = serializers.CharField(required=False)
    account = serializers.CharField(required=False)
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
