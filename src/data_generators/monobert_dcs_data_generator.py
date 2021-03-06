import tensorflow.keras as keras
import tables
import numpy as np
import random

class DataGeneratorDCSMonoBERT(keras.utils.Sequence):
    def __init__(self, tokens_path, desc_path, batch_size, init_pos, last_pos, max_length, tokenizer, vocab_code, vocab_desc):
        self.tokens_path = tokens_path
        self.desc_path = desc_path
        self.batch_size = batch_size
        self.max_length = max_length
        self.tokenizer = tokenizer
        self.vocab_code = vocab_code
        self.vocab_desc = vocab_desc

        # code
        code_table = tables.open_file(tokens_path)
        self.code_data = code_table.get_node('/phrases')[:].astype(np.int)
        self.code_index = code_table.get_node('/indices')[:]
        self.full_data_len = self.code_index.shape[0]

        #self.full_data_len = 100 #100000

        # desc
        desc_table = tables.open_file(desc_path)
        self.desc_data = desc_table.get_node('/phrases')[:].astype(np.int)
        self.desc_index = desc_table.get_node('/indices')[:]

        self.init_pos = init_pos
        self.last_pos = min(last_pos, self.full_data_len)

        self.data_len = self.last_pos - self.init_pos
        print("First row", self.init_pos, "last row", self.last_pos, "len", self.__len__())

    def __len__(self):
        return (np.ceil((self.last_pos - self.init_pos) / float(self.batch_size))).astype(np.int)

    def __getitem__(self, idx):

        start_offset = idx * self.batch_size
        start_offset = start_offset % self.data_len
        chunk_size = self.batch_size

        tokenized_ids = []
        tokenized_mask = []
        tokenized_type = []
        labels = []

        for offset in range(self.init_pos + start_offset, self.init_pos + start_offset + chunk_size):
            offset = offset % self.full_data_len

            # CODE
            len, pos = self.code_index[offset]['length'], self.code_index[offset]['pos']
            extracted_code = self.code_data[pos:pos + len].copy()

            code =  (" ".join([self.vocab_code[x] for x in extracted_code]))

            # Desc
            if offset % 2 == 0:
                len, pos = self.desc_index[offset]['length'], self.desc_index[offset]['pos']
                extracted_desc = self.desc_data[pos:pos + len].copy()
                labels.append([1])
            else:
                # A half of the entries are going to be negative examples
                random_index = random.randint(0, self.full_data_len - 1)
                len, pos = self.desc_index[random_index]['length'], self.desc_index[random_index]['pos']
                extracted_desc = self.desc_data[pos:pos + len].copy()
                labels.append([0])

            desc = ( (" ".join([self.vocab_desc[x] for x in extracted_desc])) )

            input_ids, attention_mask, token_type_ids, = self.__tokenize(desc, code)

            tokenized_ids.append(input_ids)
            tokenized_mask.append(attention_mask)
            tokenized_type.append(token_type_ids)

        return [np.array(tokenized_ids),
                np.array(tokenized_mask),
                np.array(tokenized_type)
                ], np.array(labels)

    def test(self, idx):
        return self.__getitem__(idx)

    def len(self):
        return self.__len__()

    def __tokenize(self, desc, code):
        return DataGeneratorDCSMonoBERT.tokenize_sentences(self.tokenizer, self.max_length, desc, code)

    @staticmethod
    def tokenize_sentences(tokenizer, max_length, input1_str, input2_str):
        tokenized = tokenizer.batch_encode_plus(
            [[input1_str, input2_str]],
            add_special_tokens=True,
            max_length=max_length,
            return_attention_mask=True,
            return_token_type_ids=True,
            return_tensors="np",
            padding='max_length'
        )

        return tokenized["input_ids"][0], tokenized["attention_mask"][0], tokenized["token_type_ids"][0]