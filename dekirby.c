#include <stdio.h>
#include <stdlib.h>


#define RC_LOG_LEVEL_3

#ifdef RC_LOG_LEVEL_3
#define RC_LOG_LEVEL_2
#endif

#ifdef RC_LOG_LEVEL_2
#define RC_LOG_LEVEL_1
#endif

typedef unsigned char uint8;

typedef struct {
    uint8 *data;
    int offset;
    int max_size;
} r_array;

typedef struct {
    uint8 *data;
    int offset;
    int max_size;
    int eof_flag;
} byte_reader;

uint8 readbyte(byte_reader *br);
int check_eof(byte_reader *br);

r_array *init_r_array();
void insert_byte_r_array(r_array *r,uint8 b);
uint8 get_byte_r_array(r_array *r,int index);
uint8 reverse_bits(uint8 b);

int decompress(uint8 *data,uint8 **decompressed_data, int max_size,int *decompressed_size,int *compressed_size, int format);

int main(int argc,char *argv[]){
    if(argc < 3){
        printf("Usage: retrouncompress <infilename> <outfilename> <offset>\n");
        printf("\tHexidecimal Offsets must be preceeded by 0x\n");
        printf("\tIf offset is ommited, it is assumed 0\n");
        return 0;
    }

    char *infilename, *outfilename;
    int offset=0;
    infilename = argv[1];
    outfilename = argv[2];

    if(argc >= 3)
        if(argv[3][0] == '0' && (argv[3][1] == 'x' || argv[3][1] == 'X'))
            sscanf(&argv[3][2],"%x",&offset);
        else offset=atoi(argv[3]);

    printf("%s %s %d\n",infilename,outfilename,offset);
    
    //open the input file pointer
    FILE *infp = fopen(infilename,"rb");
    if(infp == NULL){
        printf("Error: Opening Input File.\n");
        return 1;
    }

    //find end of file
    fseek(infp,0,SEEK_END);
    int file_size = ftell(infp);

    if(offset > file_size){
        printf("Error: Offset beyond end of file.\n");
        return 1;
    }
    //lazily read entire file into memory...
    fseek(infp,0,SEEK_SET);
    uint8 *data=(uint8 *)malloc(file_size);
    fread(data,file_size,1,infp);

    int decompressed_size, compressed_size;
    printf("offset %x, %02x\n\n",offset,*(data+offset));
    uint8 *decompressed_data;
    int status = decompress(data+offset,&decompressed_data,file_size-offset,&decompressed_size,&compressed_size,0);
    
    fclose(infp);
    
    if(status != 1){
        printf("Decompression Error... Aborting...\n");
        free(data);
        return 1;
    }


    printf("Compressed Data Size: %d\n",compressed_size);
    printf("Decompressed Data Size: %d\n",decompressed_size);

    //write outputdata to file
    FILE *outfp = fopen(outfilename,"wb");
    if(outfp == NULL){
        printf("Error: Opening Output File.\n");
        return 1;
    }

    fwrite(decompressed_data,decompressed_size,1,outfp);
    fclose(outfp);

    free(decompressed_data);
    free(data);
}

int decompress(uint8 *data,uint8 **decompressed_data, int max_size,int *decompressed_size,int *compressed_size, int format){

    byte_reader *br = (byte_reader *)malloc(sizeof(byte_reader));
    
    br->data = data;
    br->max_size = max_size;
    br->offset = 0;
    br->eof_flag = 0;

    //init the resiziable array for our output
    r_array *out_array = init_r_array();
    
    int i;

    int commands_used[7];
    for(i = 0;i < 7;i++)commands_used[i] = 0;
    int command_count=0;

    while(1){
        int old_offset = br->offset;
        int old_out_offset = out_array->offset;

        int header_byte;
        header_byte = readbyte(br);
        //0xFF is allowed to be the last byte in the file, anything else can't be.
        if(header_byte == 0xff)break;
        if(check_eof(br))goto error;

        int command, length;

        command = header_byte >> 5;
        length = (header_byte&0x1f) + 1;
        
        //if this is an extended command (type 111)
        if(command == 7){
            //get command and length from 2 byte header
            int header_byte2 = readbyte(br);
            if(check_eof(br))goto error;
            command = (header_byte>>2)&0x07;
            length = header_byte2 + ((header_byte&0x03)<<8) + 1;
        }

#ifdef RC_LOG_LEVEL_2
        printf("header_byte %02x command %d length %d\n",header_byte,command,length);
#endif

        uint8 b;
        uint8 b2;
        int addr;
        switch(command){
            case 0:
                //copy input to output
                for(i = 0;i < length;i++){
                    b = readbyte(br);
                    if(check_eof(br))goto error;
                    insert_byte_r_array(out_array,b);
                }
                commands_used[0]++;
                break;
            case 1:
                b = readbyte(br);
                if(check_eof(br))goto error;
                for(i = 0;i < length;i++){
                    insert_byte_r_array(out_array,b);
                }
                commands_used[1]++;
                break;
            case 2:
                b = readbyte(br);
                if(check_eof(br))goto error;
                b2 = readbyte(br);
                if(check_eof(br))goto error;
                for(i = 0;i < length;i++){
                    insert_byte_r_array(out_array,b);
                    insert_byte_r_array(out_array,b2);
                }
                commands_used[2]++;
                break;
            case 3:
                b = readbyte(br);
                if(check_eof(br))goto error;
                for(i = 0;i < length;i++){
                    insert_byte_r_array(out_array,b);
                    b++;
                }
                commands_used[3]++;
                break;

            //parasyte's doc reports that 4 and 7(invalid type) do the same thing
            case 4:
            case 7:
                //data contains an address to copy data from
                addr = readbyte(br) << 8;
                if(check_eof(br))goto error;
                addr |= readbyte(br);
                if(check_eof(br))goto error;
                for(i = 0;i < length;i++){
                    b = get_byte_r_array(out_array,addr++);
                    insert_byte_r_array(out_array,b);
                }
                commands_used[4]++;
                break;

            case 5:
                addr = readbyte(br) << 8;
                if(check_eof(br))goto error;
                addr |= readbyte(br);
                if(check_eof(br))goto error;
                for(i = 0;i < length;i++){
                    b = get_byte_r_array(out_array,addr++);
                    insert_byte_r_array(out_array,reverse_bits(b));
                }
                commands_used[5]++;
                break;
            case 6:
                addr = readbyte(br) << 8;
                if(check_eof(br))goto error;
                addr |= readbyte(br);
                if(check_eof(br))goto error;
                for(i = 0;i < length;i++){
                    b = get_byte_r_array(out_array,addr--);
                    if(addr < 0){
#ifdef RC_LOG_LEVEL_1
                        printf("Decompression Error: Compression type 6 moved below beginning of input stream\n");
#endif
                        goto error;
                    }
                    insert_byte_r_array(out_array,b);
                }
                commands_used[6]++;
                break;
        }
        command_count++;

#ifdef RC_LOG_LEVEL_3
    int j;
    printf("Compressed: ");
    for(i = old_offset;i < br->offset;i++){
        printf("%02X",br->data[i]);
    }
    printf("\n");

    printf("Uncompressed: ");
    for(j = old_out_offset;j < out_array->offset;j++){
        printf("%02X",out_array->data[j]);
    }
    printf("\n");
#endif
    }

    //get the compressed size
    *compressed_size=br->offset;

    *decompressed_size = out_array->offset;


    //delete the resizable array, saving the data
    *decompressed_data = out_array->data;

    free(out_array);
    free(br);

#ifdef RC_LOG_LEVEL_1
    printf("Decompression Finished... some stats: \n");
    printf("-Compression Header Command Used Count-\n");
    printf("0\t1\t2\t3\t4\t5\t6\n");
    printf("%d\t%d\t%d\t%d\t%d\t%d\t%d\n",commands_used[0],commands_used[1],commands_used[2]
        ,commands_used[3],commands_used[4],commands_used[5],commands_used[6]);
    printf("total blocks: %d\n",command_count);
    printf("compressed size: %d\n",*compressed_size);
    printf("decompressed size: %d\n",*decompressed_size);
#endif

    return 1;

    error:;

    return 0;
}

uint8 readbyte(byte_reader *br){
    uint8 b = br->data[br->offset++];

    return b;
}

//This error checking function ensures the end of file hasn't been
//reached before it is supposed to.
int check_eof(byte_reader *br){
    //check eof flag... this indicates error
    if(br->offset >= br->max_size){
#ifdef RC_LOG_LEVEL_1
        printf("Decompression Error: max_size reached. Aborting.\n");
#endif
        return 1;
    }
    return 0;
}


//    -------Resizable Array Functions--------

//initiailize a new resizable array
r_array *init_r_array(){
    r_array *r = (r_array *)malloc(sizeof(r_array));
    r->offset = 0;
    r->max_size = 65536;
    r->data = (uint8 *)malloc(65536);
}

//insert a byte into resizable array
void insert_byte_r_array(r_array *r,uint8 b){
    r->data[r->offset++] = b;
    if(r->offset == r->max_size){
        r->max_size += 65536;
        r->data = (uint8 *)realloc((void *)r->data,r->max_size);
    }
}

//get a byte from a resizable array
uint8 get_byte_r_array(r_array *r,int index){
    return r->data[index];
}


uint8 reverse_bits(uint8 b){
    int i;
    uint8 r=0;
    for(i = 0;i < 8;i++){
        r<<=1;
        if(b&1)r|=0x01;
        b>>=1;
    }
    return r;
}

