
import sys
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "2"
import tensorflow as tf
from tensorflow.keras import backend as K
import numpy as np
import pandas as pd
import pathlib

from .data_generators.dcs_data_generator import DataGeneratorDCS
from . import CodeSearchManager, help


class SNN_DCS(CodeSearchManager):

    def __init__(self, data_path, data_chunk_id=0):
        self.data_path = data_path

        # dataset info
        self.total_length = 18223872
        self.chunk_size = 600000  # 18223872  # 10000


        number_chunks = self.total_length / self.chunk_size - 1
        self.number_chunks = int(number_chunks + 1 if number_chunks > int(number_chunks) else number_chunks)

        self.vocab_tokens, self.vocab_desc = None, None
        self.inverse_vocab_tokens, self.inverse_vocab_desc = None, None

        self.data_chunk_id = min(data_chunk_id, int(self.number_chunks))
        print("### Loading SNN model with DCS chunk number " + str(data_chunk_id) + " [0," + str(number_chunks)+"]")

    def get_dataset_meta_hardcoded(self):
        return 410, 13645

    def get_dataset_meta(self):
        # 18223872 (len) #1000000
        code_vector = help.load_hdf5(data_path + "train.tokens.h5", 0, 18223872)
        desc_vector = help.load_hdf5(data_path + "train.desc.h5", 0, 18223872)
        vocabulary_merged = help.load_pickle(data_path + "vocab.merged.pkl")

        longer_code = max(len(t) for t in code_vector)
        print("longer_code", longer_code)
        longer_desc = max(len(t) for t in desc_vector)
        print("longer_desc", longer_desc)

        longer_sentence = max(longer_code, longer_desc)

        number_tokens = len(vocabulary_merged)

        return longer_sentence, number_tokens

    def get_vocabularies(self):
        self.inverse_vocab_tokens = help.load_pickle(self.data_path + "vocab.merged.pkl")
        self.vocab_tokens = {y: x for x, y in self.inverse_vocab_tokens.items()}

        self.inverse_vocab_desc = help.load_pickle(self.data_path + "vocab.merged.pkl")
        self.vocab_desc = {y: x for x, y in self.inverse_vocab_desc.items()}

        return self.vocab_tokens, self.vocab_desc

    def generate_model(self, embedding_size, number_tokens, sentence_length, hinge_loss_margin):
        input_layer = tf.keras.Input(shape=(sentence_length,), name="input")
        embedding_layer = tf.keras.layers.Embedding(number_tokens, embedding_size, name="embeding")(input_layer)

        attention_layer = tf.keras.layers.Attention(name="attention")([embedding_layer, embedding_layer])

        sum_layer = tf.keras.layers.Lambda(lambda x: K.sum(x, axis=1), name="sum")(attention_layer)
        # average_layer = tf.keras.layers.Lambda(lambda x: K.mean(x, axis=1), name="average")( attention_layer)

        embedding_model = tf.keras.Model(inputs=[input_layer], outputs=[sum_layer], name='siamese_model')

        input_code = tf.keras.Input(shape=(sentence_length,), name="code")
        input_desc = tf.keras.Input(shape=(sentence_length,), name="desc")
        input_bad_desc = tf.keras.Input(shape=(sentence_length,), name="bad_desc")

        output_code = embedding_model(input_code)
        output_desc = embedding_model(input_desc)
        output_bad_desc = embedding_model(input_bad_desc)

        cos_good_sim = tf.keras.layers.Dot(axes=1, normalize=True, name='cos_good_sim')([output_code, output_desc])

        cos_model = tf.keras.Model(inputs=[input_code, input_desc], outputs=[cos_good_sim],
                                        name='cos_model')

        # Used in tests
        embedded_code = tf.keras.Input(shape=(output_code.shape[1],), name="embedded_code")
        embedded_desc = tf.keras.Input(shape=(output_code.shape[1],), name="embedded_desc")

        dot = tf.keras.layers.Dot(axes=1, normalize=True)([embedded_code, embedded_desc])
        dot_model = tf.keras.Model(inputs=[embedded_code, embedded_desc], outputs=[dot],
                                        name='dot_model')

        cos_bad_sim = tf.keras.layers.Dot(axes=1, normalize=True, name='cos_bad_sim')([output_code, output_bad_desc])

        loss = tf.keras.layers.Lambda(lambda x: K.maximum(1e-6, hinge_loss_margin - x[0] + x[1]),
                                      output_shape=lambda x: x[0],
                                      name='loss')([cos_good_sim, cos_bad_sim])

        training_model = tf.keras.Model(inputs=[input_code, input_desc, input_bad_desc], outputs=[loss],
                                        name='training_model')

        opt = tf.keras.optimizers.Adam(learning_rate=0.001)

        training_model.compile(loss=lambda y_true, y_pred: y_pred + y_true - y_true, optimizer=opt)
        # y_true-y_true avoids warning
        self.training_model, self.code_model, self.desc_model, self.dot_model = training_model, embedding_model, embedding_model, dot_model
        return training_model, embedding_model, cos_model, dot_model



    def generate_embeddings(self, number_of_elements=100):
        test_tokens = help.load_hdf5(self.data_path + "test.tokens.h5" , 0, number_of_elements)
        test_desc = help.load_hdf5(self.data_path + "test.desc.h5" , 0, number_of_elements) # 10000

        longer_sentence, number_tokens = self.get_dataset_meta_hardcoded()

        test_tokens = help.pad(test_tokens, longer_sentence)
        test_desc = help.pad(test_desc, longer_sentence)

        embedded_tokens = []
        embedded_desc = []
        print("Embedding tokens...")
        for idx, token in enumerate(test_tokens):

            embedded_tokens.append(self.code_model.predict(np.array(test_tokens[idx]).reshape(1,-1))[0])
            embedded_desc.append(self.desc_model.predict(np.array(test_desc[idx]).reshape(1,-1))[0])

        return embedded_tokens, embedded_desc

    def test(self, embedding_model, dot_model, results_path, code_length, desc_length, number_of_elements=100):
        embedded_tokens, embedded_desc = self.generate_embeddings(number_of_elements)

        self.test_embedded(embedded_tokens, embedded_desc, results_path)
        df = pd.read_csv(self.data_path + "descriptions.csv", header=0)
        df = df.dropna()
        df = df[df["rowid"] < number_of_elements]

        self.rephrasing_test(df, embedded_tokens, embedded_desc)

    def tokenize_desc(self, sentence):
        tokenized = []

        for word in sentence.split(" "):
            if word in self.inverse_vocab_desc:
                tokenized.append(self.inverse_vocab_desc[word])
            else:
                tokenized.append(self.inverse_vocab_desc["UNK"])

        return help.pad(np.array(tokenized).reshape((1,-1)), 410)

    def rephrasing_test(self, rephrased_descriptions_df, embedded_tokens, embedded_desc):

        rephrased_ranking = {}
        new_ranking = {}
        for i, row in enumerate(rephrased_descriptions_df.iterrows()):
            idx = row[1].values[0]

            original_desc = row[1].values[1]

            embedded_tokens_copy = embedded_tokens.copy()
            embedded_desc_copy = embedded_desc.copy()

            original_rank = self.get_id_rank(idx, embedded_tokens_copy, embedded_desc_copy)

            desc = row[1].values[2]

            desc_ = self.tokenize_desc(desc)

            embedded_desc_copy[idx] = self.desc_model.predict(np.array(desc_).reshape(1, -1))[0]


            new_rank = self.get_id_rank(idx, embedded_tokens_copy, embedded_desc_copy)

            rephrased_ranking[idx] = original_rank
            new_ranking[idx] = new_rank

        print("Number of queries: ",str(len(rephrased_descriptions_df.index)))
        print("Selected topN:")
        print(self.get_top_n(1, rephrased_ranking))
        print(self.get_top_n(3, rephrased_ranking))
        print(self.get_top_n(5, rephrased_ranking))

        print("Rephrased topN:")
        print(self.get_top_n(1, new_ranking))
        print(self.get_top_n(3, new_ranking))
        print(self.get_top_n(5, new_ranking))
        return rephrased_ranking, new_ranking

    def training_data_chunk(self, id, valid_perc):

        init_trainig = self.chunk_size * id
        init_valid = int(self.chunk_size * id + self.chunk_size * valid_perc)
        end_valid = int(self.chunk_size * id + self.chunk_size)

        return init_trainig, init_valid, end_valid


    def load_dataset(self, data_chunk_id, batch_size):

        init_trainig, init_valid, end_valid = self.training_data_chunk(data_chunk_id, 0.8)

        longer_sentence, number_tokens = self.get_dataset_meta_hardcoded()

        training_set_generator = DataGeneratorDCS(self.data_path + "train.tokens.h5", self.data_path + "train.desc.h5",
                                                  batch_size, init_trainig, init_valid, longer_sentence, longer_sentence)
        return training_set_generator

if __name__ == "__main__":

    print("Running SNN Model")

    args = sys.argv
    data_chunk_id = 0
    if len(args) > 1:
        data_chunk_id = int(args[1])

    script_path = str(pathlib.Path(__file__).parent)

    data_path = script_path + "/../data/deep-code-search/processed/"

    snn_dcs = SNN_DCS(data_path, data_chunk_id)

    BATCH_SIZE = 32 * 4 * 2

    dataset = snn_dcs.load_dataset(0, BATCH_SIZE)

    longer_sentence, number_tokens = snn_dcs.get_dataset_meta_hardcoded()

    snn_dcs.get_vocabularies()

    embedding_size = 2048

    multi_gpu = False

    print("Building model and loading weights")
    if multi_gpu:
        tf.debugging.set_log_device_placement(False)

        strategy = tf.distribute.MirroredStrategy()

        with strategy.scope():
            training_model, embedding_model, cos_model, dot_model = snn_dcs.generate_model(embedding_size, number_tokens, longer_sentence, 0.5)
            #snn_dcs.load_weights(script_path+"/../weights/snn_dcs_weights")
    else:
        training_model, embedding_model, cos_model, dot_model = snn_dcs.generate_model(embedding_size, number_tokens,
                                                                                       longer_sentence, 0.6)
        snn_dcs.load_weights(script_path + "/../final_weights/snn_600000_dcs_weights")

    #snn_dcs.train(dataset, script_path+"/../weights/snn_600000_dcs_weights")

    snn_dcs.test(embedding_model, dot_model, script_path+"/../results/snn_600000_dcs", longer_sentence, longer_sentence, 200)

    #snn_dcs.test(embedding_model, dot_model, script_path+"/../results", longer_sentence, longer_sentence, 200)


