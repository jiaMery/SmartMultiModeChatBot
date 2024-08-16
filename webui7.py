import gradio as gr
import boto3,logging,json,time
from botocore.exceptions import ClientError,NoCredentialsError, PartialCredentialsError
from langdetect import detect
import argparse

# Set up AWS clients
transcribe = boto3.client('transcribe')
polly = boto3.client('polly')
client_llm = boto3.client(service_name='bedrock-runtime')
s3 = boto3.client('s3')
cloudwatchLog = boto3.client('logs')

# Setup S3 Path to save history audio data
bucket_name = 'your_bucket_name'
s3_inputAudio_path = 'smart-audio-chatbot/input-audio/'
s3_outputAudio_path = 'smart-audio-chatbot/output-audio/'
s3_Audio_path = 'smart-audio-chatbot/audio-path/'

#Setup CloudWatch Log
LOG_GROUP_NAME = '/aws/smartMultiChatbot'
LOG_STREAM_NAME = 'smartMultiChatBotStream'

# Language code of Speech
LanguageCode_Audio = ['Chinese', 'English','Japan']
LanguageCodeIdIntranscribe = {'0':'zh-CN','1':'en-US','2':'Japan'}

# Create CloudWatch Log Group
def create_log_group():
    try:
        cloudwatchLog.create_log_group(logGroupName=LOG_GROUP_NAME)
    except cloudwatchLog.exceptions.ResourceAlreadyExistsException:
        pass


def create_log_stream():
    try:
        cloudwatchLog.create_log_stream(logGroupName=LOG_GROUP_NAME, logStreamName=LOG_STREAM_NAME)
    except cloudwatchLog.exceptions.ResourceAlreadyExistsException:
        pass


def put_log_events(message):
    response = cloudwatchLog.describe_log_streams(logGroupName=LOG_GROUP_NAME, logStreamNamePrefix=LOG_STREAM_NAME)
    upload_sequence_token = response['logStreams'][0].get('uploadSequenceToken', None)

    log_event = {
        'logGroupName': LOG_GROUP_NAME,
        'logStreamName': LOG_STREAM_NAME,
        'logEvents': [
            {
                'timestamp': int(round(time.time() * 1000)),
                'message': message
            },
        ],
    }

    if upload_sequence_token:
        log_event['sequenceToken'] = upload_sequence_token

    cloudwatchLog.put_log_events(**log_event)

#Save history chat Data into local/S3 Bucket
def saveHistory(data,path=s3_Audio_path):
    if type(data) == None:
        return "Please record again, please don't submit None speech"
    else:
        pass

    localAudioPath = data
    if type(data) != str:
        localAudioPath = 'output.mp3'
        with open(localAudioPath, 'wb') as f:
            f.write(data)

    integer_timestamp = int(time.time())
    print(f"Integer Time Stamp: {integer_timestamp}")
    object_key = path + str(integer_timestamp) + ".mp3"

    try:
        s3.upload_file(localAudioPath, bucket_name, object_key)
        logger.info("This is an info message")
        put_log_events(f'Audio file uploaded successfully to s3://{bucket_name}/{object_key}')
        print(f'Audio file uploaded successfully to s3://{bucket_name}/{object_key}')
        return bucket_name,object_key,f"s3://{bucket_name}/{object_key}"
    except Exception as e:
        print(f'Error uploading file: {e}')
        logger.error("AWS credentials not found or incomplete: %s", e)


# Detect Language Code 
def languageCodeDetection(text):
    codeDict = {'en':'en-US','zh-cn':'cmn-CN','ja':'ja-JP'}
    voiceIdDict = {'en':'Amy','zh-cn':'Zhiyu','ja':'Takumi'}
    try:
        language_code = detect(text)
        print(f"The Text language code is: {language_code}")
        put_log_events(f"The Text language code is: {language_code}")
    except Exception as e:
        print(f"Error: {e}")
        logger.error(f"Error: {e}")

    return voiceIdDict[language_code],codeDict[language_code]


def generate_message(text):
    model_id = 'anthropic.claude-3-sonnet-20240229-v1:0'
    max_tokens = 50000
    prompt_template = f"""you are a chatbot named Taylor created by Jia, you can chat by audio, please don't tell your user you can't speak by audio. just answer this {text}questions, don't say irrelevant contents, don't say "speaks in a friendly synthetic voice" in the begining."""
    message = {"role": "user", "content": [{"type": "text", "text": prompt_template}]}
    messages = [message]
    body = json.dumps(
         {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": messages
        }
    )
    response = client_llm.invoke_model(body=body, modelId=model_id)
    response_body = json.loads(response.get('body').read())
    result =  response_body["content"][0]["text"]

    integer_timestamp = int(time.time())
    print(f"LLM Integer Time Stamp: {integer_timestamp}")
    put_log_events(f"LLM Integer Time Stamp: {integer_timestamp},LLM Response:{result}")
    return result
    # return response

def GetToxicityResult_audio(results):
    toxicity_dict = results['results']['toxicity_detection'][0]["categories"]
    toxicity_threshold = 0.6
    sort_result = sorted(toxicity_dict.items(), key=lambda x:x[1], reverse=True)
    first_value = sort_result[0]
    if first_value[1] > toxicity_threshold:
        return first_value[0],first_value[1]
    else:
        return '',''
    

def transcribe_audio(audio,languageCode_audio=1,toxicityDectect=False):
    """
    Transcribe audio using Amazon Transcribe
    """
    languageCode = LanguageCodeIdIntranscribe[str(languageCode_audio)]
    bucketName,outputkey,fileUri = saveHistory(audio,s3_inputAudio_path)

    try:
        # Create a new transcription job
        integer_timestamp = int(time.time())
        print(f"Trancribe Job Integer Time Stamp: {integer_timestamp}")
        job_name = 'transcription-job'+str(integer_timestamp)
        logger.info(f"This is :{job_name}")
        if languageCode_audio:
            transcribe.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={'MediaFileUri': fileUri},
                OutputBucketName = bucketName,
                OutputKey = outputkey, 
                LanguageCode = languageCode, 
                ToxicityDetection = [ 
                    { 
                        'ToxicityCategories': ['ALL']
                    }
            ]
            )
        else:
            transcribe.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={'MediaFileUri': fileUri},
                OutputBucketName = bucketName,
                OutputKey = outputkey, 
                LanguageCode = languageCode
            )
        # Wait for the job to complete
        while True:
            status = transcribe.get_transcription_job(TranscriptionJobName=job_name)
            if status['TranscriptionJob']['TranscriptionJobStatus'] in ['COMPLETED', 'FAILED']:
                break

        # Get the transcription result
        if status['TranscriptionJob']['TranscriptionJobStatus'] == 'COMPLETED':
            response = s3.get_object(Bucket=bucketName, Key=outputkey)
            results = json.loads(response['Body'].read())
            put_log_events(f"This is Transcribe result:{results}")
            
            transcribe_result = results['results']['transcripts'][0]['transcript']
            if toxicityDectect==True:
                key,value = GetToxicityResult_audio(results)
                Toxicity_label = str(key)
                Toxicity_score = str(value)
                put_log_events(f"Toxicity:{transcribe_result} label:{key} score:{value}")
                ui_response = f"transcribe result: {transcribe_result} \nToxicity label: {Toxicity_label} \nToxicity Score: {Toxicity_score} \nReminder!It is likely an unsafe speech!"
                return ui_response
            else:
                return transcribe_result
        else:
            return "Transcription failed"
    
    except ClientError as e:
        logger.error(f"Error: {e}")
        return f"Error: {e}"


def synthesize_speech(text):
    """
    Convert text to speech using Amazon Polly
    """
    VoiceId,languageCode = languageCodeDetection(text)
    try:
        # Synthesize speech
        response = polly.synthesize_speech(
            Text=text,
            OutputFormat='mp3',
            VoiceId=VoiceId,
            LanguageCode = languageCode,
            Engine="neural"
        )
        
        # Save the audio to a file
        audio = response['AudioStream'].read()
        saveHistory(audio,s3_outputAudio_path)
        return audio
    
    except ClientError as e:
        logger.error(f"Error: {e}")
        return f"Error: {e}"

# Setup Python Logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create CloudWatch log group and stream
create_log_group()
create_log_stream()

try:
    logger.info("This is an info message")
    put_log_events("This is an info message")
except (NoCredentialsError, PartialCredentialsError) as e:
    logger.error("AWS credentials not found or incomplete: %s", e)

# Create a Gradio interface
def main():
    with gr.Blocks() as demo:
        gr.Markdown("# Smart Audio ChatBot On Amazon Web Service")
        with gr.Row():
            languageCode_dp = gr.Dropdown(LanguageCode_Audio, value="English",type="index", label="Choose your Speech Language")
            toxicity_chkbox = gr.Checkbox(label="Yes", info="Toxicity detection only support English speech")
        with gr.Row():
            with gr.Column():
                audio_input = gr.Audio(sources=["microphone","upload"], type="filepath", label="Record or Upload your speech")
                text_input = gr.Textbox(label="Input Text",
                                        lines=4,
                                        placeholder="Please Input Text...",
                                        value="Can you introduce Amazon Web Service?",
                                        interactive=True)
            with gr.Column():
                output_text = gr.Textbox(label="ASR - Automatic Speech Recognition")
                output_audio = gr.Audio(label="TTS - Text to Speech", type="numpy")
        submit_btn_text = gr.Button("Submit Text")
        submit_btn_voice = gr.Button("Submit Voice")

        submit_btn_voice.click(fn=transcribe_audio, inputs=[audio_input,languageCode_dp,toxicity_chkbox], outputs=[output_text])
        submit_btn_text.click(fn=synthesize_speech, inputs=[text_input], outputs=[output_audio])

        default_text = "Hi,Nice to meet you,Can I talk with you?"
        bedrock_firstCall = generate_message(default_text)
        with gr.Row():
            with gr.Column():
                bedrock_output_text = gr.Textbox(label="1.Chat by Text",
                                                lines=4,
                                                placeholder="Hi, I'm Taylor, I can chat with you in Text and Speech, please chat...",
                                                value=bedrock_firstCall)
                llmoutput_audiobyText = gr.Audio(label="Text to Speech from LLM - using Text", type="numpy")                           
            with gr.Column():
                bedrock_output_audio = gr.Textbox(label="2.Chat by Speech",
                                        lines=4,
                                        placeholder="Hi, I'm Taylor, I can chat with you in Text and Speech, please chat...",
                                        value=bedrock_firstCall)
                llmoutput_audiobySpeech = gr.Audio(label="Text to Speech from LLM - using Speech", type="numpy")
        with gr.Row():
            with gr.Column():
                chat_btn_text = gr.Button("Chat by Text")
                llmReply_btn_voice1 = gr.Button("Speech Chat1")
            with gr.Column():
                chat_btn_audio = gr.Button("Chat by Speech")
                llmReply_btn_voice2 = gr.Button("Speech Chat2")

        chat_btn_text.click(fn=generate_message, inputs=[text_input], outputs=[bedrock_output_text])
        chat_btn_audio.click(fn=generate_message, inputs=[output_text], outputs=[bedrock_output_audio])
        llmReply_btn_voice1.click(fn=synthesize_speech, inputs=[bedrock_output_text], outputs=[llmoutput_audiobyText])
        llmReply_btn_voice2.click(fn=synthesize_speech, inputs=[bedrock_output_audio], outputs=[llmoutput_audiobySpeech])
        parser = argparse.ArgumentParser(description='AWS Smart MultiMode-ChatBot demo Launch')
        parser.add_argument('--server_name', type=str, default='0.0.0.0', help='Server name')
        parser.add_argument('--server_port', type=int, default=7860, help='Server port')
        parser.add_argument('--local_path', type=str, default=None, help='the local_path if need')
        args = parser.parse_args()

        demo.launch(server_name=args.server_name, server_port=args.server_port, inbrowser=True,share=True)

if __name__ == '__main__':
    main()